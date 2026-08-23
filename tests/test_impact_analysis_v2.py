"""Impact Analysis V2 契约与质量门禁测试。"""

from datetime import datetime, timezone

from app.analysis.mechanisms import ImpactCritic, MechanismGenerator
from app.analysis.quality import validate_impact_output
from app.analysis.schemas import ImpactAnalysisOutputV2
from app.analysis.service import ImpactAnalysisService, _legacy_graph, _legacy_projection
from app.domain import Event, ImpactAnalysis
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository


def _output(**overrides):
    payload = {
        "summary": "加息通过融资成本影响地产估值。",
        "causal_graph": {
            "nodes": [
                {"node_id": "node_event", "node_type": "event", "label": "加息"},
                {"node_id": "node_cost", "node_type": "variable", "label": "融资成本"},
                {"node_id": "node_target", "node_type": "impact", "label": "地产"},
            ],
            "edges": [
                {
                    "edge_id": "edge_1",
                    "source_node_id": "node_event",
                    "target_node_id": "node_cost",
                    "mechanism": "政策利率上升",
                    "direction": "negative",
                    "inference_kind": "fact",
                    "confidence": 0.9,
                },
                {
                    "edge_id": "edge_2",
                    "source_node_id": "node_cost",
                    "target_node_id": "node_target",
                    "mechanism": "融资成本传导",
                    "direction": "negative",
                    "inference_kind": "inference",
                    "confidence": 0.7,
                },
            ],
        },
        "scenarios": [
            {
                "scenario_id": "scn_base",
                "name": "base",
                "assumptions": ["政策维持当前路径"],
                "active_edge_ids": ["edge_1", "edge_2"],
            }
        ],
        "impact_assessments": [
            {
                "assessment_id": "ia_1",
                "scenario_id": "scn_base",
                "target_type": "sector",
                "target_name": "房地产",
                "exposure_path": ["融资成本", "地产开发商"],
                "dimensions": [
                    {"dimension": "valuation", "direction": "negative", "magnitude": "moderate"}
                ],
                "horizon": "1_4w",
                "confidence": 0.7,
            }
        ],
        "quality_report": {"evidence_coverage": 1.0},
    }
    payload.update(overrides)
    return ImpactAnalysisOutputV2.model_validate(payload)


def test_v2_contract_and_quality_gate_pass() -> None:
    output = _output()
    assert output.schema_version == "2.0.0"
    assert validate_impact_output(output) == []


def test_quality_gate_rejects_dangling_edge_and_low_evidence() -> None:
    output = _output(
        causal_graph={
            "nodes": [
                {"node_id": "node_event", "node_type": "event", "label": "加息"}
            ],
            "edges": [
                {
                    "edge_id": "edge_bad",
                    "source_node_id": "node_event",
                    "target_node_id": "node_missing",
                    "mechanism": "缺失节点",
                    "direction": "uncertain",
                    "inference_kind": "assumption",
                    "confidence": 0.2,
                }
            ],
        },
        quality_report={"evidence_coverage": 0.4},
    )
    blockers = validate_impact_output(output)
    assert any("dangling causal edge" in item for item in blockers)
    assert "evidence coverage below 0.95" in blockers


def test_v2_legacy_projection_keeps_existing_contract() -> None:
    chains, impacts = _legacy_projection(_output())
    assert chains[0]["chain_id"] == "chn_v2_base"
    assert chains[0]["steps"]
    assert impacts[0]["target_name"] == "房地产"
    assert impacts[0]["horizon"] == "medium"


def test_legacy_graph_includes_impact_targets_and_compatibility_edges() -> None:
    analysis = ImpactAnalysis(
        id="imp_legacy",
        event_id="evt_legacy",
        version=1,
        status="draft",
        event_title_snapshot="央行降息",
        summary="测试",
        transmission_chains=[
            {
                "chain_id": "chn_rate",
                "mechanism": "利率传导",
                "steps": [{"step": 0, "description": "融资成本下降"}],
                "confidence": 0.7,
            }
        ],
        impacts=[
            {
                "target_type": "sector",
                "target_name": "房地产",
                "direction": "positive",
                "horizon": "medium",
                "confidence": 0.65,
                "rationale": "融资改善",
                "chain_refs": ["chn_rate"],
            },
            {
                "target_type": "sector",
                "target_name": "银行",
                "direction": "negative",
                "horizon": "medium",
                "confidence": 0.55,
                "rationale": "息差承压",
                "chain_refs": [],
            },
        ],
        macro_assumptions=[],
        watch_items=[],
        generated_by="test",
    )
    graph = _legacy_graph(analysis)
    labels = {node["label"] for node in graph["nodes"]}
    assert {"房地产", "银行"}.issubset(labels)
    fallback_edge = next(
        edge for edge in graph["edges"] if edge["target_node_id"] == "node_legacy_impact_1"
    )
    assert fallback_edge["source_node_id"] == "node_event"
    assert "未关联传导链" in fallback_edge["mechanism"]


def test_mechanism_generator_and_critic_report_quality_findings() -> None:
    output = _output(
        causal_graph={
            "nodes": [
                {"node_id": "node_event", "node_type": "event", "label": "加息"},
                {"node_id": "node_target", "node_type": "impact", "label": "房地产"},
            ],
            "edges": [
                {
                    "edge_id": "edge_self",
                    "source_node_id": "node_event",
                    "target_node_id": "node_event",
                    "mechanism": "循环",
                    "direction": "uncertain",
                    "inference_kind": "assumption",
                    "confidence": 0.9,
                }
            ],
        }
    )
    mechanism = MechanismGenerator().generate(output)
    critique = ImpactCritic().critique(output)
    assert "edge_without_evidence:edge_self" in mechanism.warnings
    assert "self_loop:edge_self" in critique.blockers


def test_service_persists_v2_graph_and_quality_report() -> None:
    repository = InMemoryRepository()
    event = Event(
        id=new_id("evt"),
        event_type="macro_policy",
        status="triaged",
        title="央行加息",
        entity_ids=[],
        document_ids=[],
        importance=0.9,
        urgency="high",
        occurred_at=datetime.now(timezone.utc),
    )
    repository.save_event(event)

    class _Agent:
        def analyze(self, *args, **kwargs):
            return _output()

    analysis = ImpactAnalysisService(repository, agent=_Agent()).generate(event.id)
    assert analysis.status == "needs_review"
    assert analysis.degraded is False
    assert analysis.analysis_payload["schema_version"] == "2.0.0"
    assert analysis.quality_report["gate_passed"] is True
    assert analysis.transmission_chains[0]["chain_id"] == "chn_v2_base"
