"""事件影响分析服务。

负责版本化生成、持久化 ``ImpactAnalysis``，并在 LLM 不可用时提供规则降级模板。
"""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from app.analysis.agents import ImpactAnalystAgent
from app.analysis.context import ImpactContextBuilder
from app.analysis.mechanisms import ImpactCritic, MechanismGenerator
from app.analysis.preliminary import PreliminaryAssessmentService
from app.analysis.quality import validate_impact_output
from app.analysis.schemas import (
    ImpactAnalysisOutput,
    ImpactAnalysisOutputV2,
    ImpactTarget,
    TransmissionChain,
)
from app.domain import AuditLog, Event, ImpactAnalysis
from app.model_gateway.service import ModelGateway
from app.platform.ids import new_id
from app.platform.repository import Repository
from app.platform.settings import Settings

IMPACT_ANALYSIS_ACTOR = "agent:impact_analyst"


class ImpactAnalysisService:
    """生成并管理事件影响分析版本链。"""

    def __init__(
        self,
        repository: Repository,
        settings: Optional[Settings] = None,
        agent: Optional[ImpactAnalystAgent] = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or Settings.from_environment()
        self.agent = agent or ImpactAnalystAgent(ModelGateway(repository))
        self.context_builder = ImpactContextBuilder(repository)
        self.mechanism_generator = MechanismGenerator()
        self.impact_critic = ImpactCritic()
        self.preliminary_service = PreliminaryAssessmentService(
            repository, gateway=getattr(self.agent, "gateway", None)
        )

    def generate(self, event_id: str, actor: Optional[str] = None) -> ImpactAnalysis:
        """为事件生成新一版影响分析；LLM 失败时使用规则模板。"""
        event = self.repository.get_event(event_id)
        if event is None:
            raise ValueError(f"event not found: {event_id}")

        context = self.context_builder.build(event)
        claims = context.claims
        fact_card = context.fact_card
        entities = context.entities
        preliminary = self.preliminary_service.generate(
            event_id, actor=actor or IMPACT_ANALYSIS_ACTOR
        )

        output = self.agent.analyze(
            event, claims, fact_card, entities,
            context={
                **context.to_payload(),
                "preliminary_assessment": preliminary.assessment_payload,
                "preliminary_assessment_id": preliminary.id,
            },
        )
        degraded = output is None
        model_failure = getattr(self.agent, "last_failure", None)
        if degraded:
            output = _fallback_template(event)

        v2_payload: dict = {}
        quality_report = {
            "gate_passed": not degraded,
            "blockers": ["model_unavailable"] if degraded else [],
            "model_failure": model_failure.as_dict() if model_failure is not None else None,
        }
        if isinstance(output, ImpactAnalysisOutputV2):
            mechanism_result = self.mechanism_generator.generate(output)
            critique = self.impact_critic.critique(output)
            blockers = validate_impact_output(output)
            quality_report = output.quality_report.model_dump()
            quality_report["blockers"] = sorted(
                set(quality_report.get("blockers", [])) | set(blockers) | set(critique.blockers)
            )
            quality_report["warnings"] = sorted(
                set(quality_report.get("warnings", []))
                | set(mechanism_result.warnings)
                | set(critique.warnings)
            )
            quality_report["gate_passed"] = not quality_report["blockers"]
            v2_payload = output.model_dump(mode="json")
            if blockers:
                degraded = True

        if isinstance(output, ImpactAnalysisOutputV2):
            transmission_chains, impacts = _legacy_projection(output)
        else:
            transmission_chains = [chain.model_dump() for chain in output.transmission_chains]
            impacts = [impact.model_dump() for impact in output.impacts]

        versions = self.repository.list_impact_analyses_for_event(event_id)
        latest = max(versions, key=lambda item: item.version, default=None)
        version = 1 if latest is None else latest.version + 1

        analysis = ImpactAnalysis(
            id=new_id("imp"),
            event_id=event_id,
            version=version,
            status="draft" if degraded else "needs_review",
            event_title_snapshot=event.title,
            summary=output.summary,
            transmission_chains=transmission_chains,
            impacts=impacts,
            macro_assumptions=list(getattr(output, "macro_assumptions", [])),
            watch_items=list(output.watch_items),
            generated_by=IMPACT_ANALYSIS_ACTOR,
            model_run_id=None if degraded else getattr(output, "model_run_id", None),
            degraded=degraded,
            supersedes_id=None,
            created_at=datetime.now(timezone.utc),
            analysis_payload=v2_payload,
            quality_report=quality_report,
            preliminary_assessment_id=preliminary.id,
        )
        self.repository.save_impact_analysis(analysis)
        self._audit("impact_analysis.generated", analysis, actor or IMPACT_ANALYSIS_ACTOR)
        return analysis

    def get_latest(self, event_id: str) -> Optional[ImpactAnalysis]:
        return self.repository.get_latest_impact_analysis_for_event(event_id)

    def get(self, impact_analysis_id: str) -> Optional[ImpactAnalysis]:
        return self.repository.get_impact_analysis(impact_analysis_id)

    def list_versions(self, event_id: str, limit: Optional[int] = None) -> list[ImpactAnalysis]:
        return self.repository.list_impact_analyses_for_event(event_id, limit=limit)

    def graph(self, impact_analysis_id: str) -> dict:
        analysis = self.get(impact_analysis_id)
        if analysis is None:
            raise ValueError("impact analysis not found")
        payload = dict(analysis.analysis_payload or {})
        if payload.get("causal_graph"):
            return {
                "schema_version": payload.get("schema_version", "2.1.0"),
                "legacy": False,
                "causal_graph": payload["causal_graph"],
                "scenarios": payload.get("scenarios", []),
                "impact_assessments": payload.get("impact_assessments", []),
                "edit_revision": analysis.edit_revision,
            }
        return {
            "schema_version": "2.1.0",
            "legacy": True,
            "causal_graph": _legacy_graph(analysis),
            "scenarios": [],
            "impact_assessments": [],
            "edit_revision": analysis.edit_revision,
        }

    def derive_draft(self, impact_analysis_id: str, actor: str) -> ImpactAnalysis:
        source = self.get(impact_analysis_id)
        if source is None:
            raise ValueError("impact analysis not found")
        versions = self.list_versions(source.event_id)
        payload = dict(source.analysis_payload or {})
        if not payload.get("causal_graph"):
            payload = self.graph(source.id)
            payload.update(
                {
                    "summary": source.summary,
                    "watch_items": source.watch_items,
                    "context_snapshot": {},
                    "quality_report": source.quality_report
                    or {"evidence_coverage": 0.0, "gate_passed": False},
                }
            )
        draft = replace(
            source,
            id=new_id("imp"),
            version=max((v.version for v in versions), default=0) + 1,
            status="draft",
            generated_by=f"user:{actor}",
            created_at=datetime.now(timezone.utc),
            analysis_payload=payload,
            quality_report=dict(source.quality_report or {}),
            edit_revision=0,
            derived_from_id=source.id,
            supersedes_id=None,
            degraded=True,
        )
        self.repository.save_impact_analysis(draft)
        self._audit("impact_analysis.draft_derived", draft, actor)
        return draft

    def edit_graph(
        self,
        impact_analysis_id: str,
        *,
        expected_revision: int,
        graph: dict,
        scenarios: list[dict],
        impact_assessments: list[dict],
        actor: str,
        change_reason: str,
    ) -> ImpactAnalysis:
        analysis = self.get(impact_analysis_id)
        if analysis is None:
            raise ValueError("impact analysis not found")
        if analysis.status != "draft":
            raise RuntimeError("IMPACT_ANALYSIS_DRAFT_REQUIRED")
        if analysis.edit_revision != expected_revision:
            raise RuntimeError("IMPACT_ANALYSIS_EDIT_CONFLICT")
        payload = dict(analysis.analysis_payload or {})
        payload.update(
            {
                "schema_version": "2.1.0",
                "causal_graph": graph,
                "scenarios": scenarios,
                "impact_assessments": impact_assessments,
            }
        )
        from app.analysis.schemas import ImpactAnalysisOutputV2

        try:
            parsed = ImpactAnalysisOutputV2.model_validate(
                {
                    "summary": analysis.summary,
                    "context_snapshot": payload.get("context_snapshot", {}),
                    "causal_graph": graph,
                    "scenarios": scenarios,
                    "impact_assessments": impact_assessments,
                    "watch_items": analysis.watch_items,
                    "quality_report": payload.get("quality_report")
                    or {"evidence_coverage": 0.0, "gate_passed": False},
                    "model_run_id": analysis.model_run_id,
                    "schema_version": "2.1.0",
                }
            )
        except Exception as exc:
            raise ValueError(f"INVALID_IMPACT_GRAPH: {exc}") from exc
        blockers = validate_impact_output(parsed)
        report = parsed.quality_report.model_dump()
        report["blockers"] = sorted(set(report.get("blockers", [])) | set(blockers))
        report["gate_passed"] = not report["blockers"]
        transmission_chains, impacts = _legacy_projection(parsed)
        updated = replace(
            analysis,
            analysis_payload=payload,
            quality_report=report,
            transmission_chains=transmission_chains,
            impacts=impacts,
            edit_revision=analysis.edit_revision + 1,
            degraded=not report["gate_passed"],
        )
        self.repository.update_impact_analysis(updated)
        self._audit("impact_analysis.graph_edited", updated, actor)
        return updated

    def _audit(self, action: str, analysis: ImpactAnalysis, actor: str) -> None:
        saver = getattr(self.repository, "save_audit_log", None)
        if not callable(saver):
            return
        saver(
            AuditLog(
                id=new_id("aud"),
                actor_id=actor,
                action=action,
                object_type="impact_analysis",
                object_id=analysis.id,
                request_id=None,
                details={
                    "event_id": analysis.event_id,
                    "version": analysis.version,
                    "degraded": analysis.degraded,
                    "model_run_id": analysis.model_run_id,
                    "impact_count": len(analysis.impacts),
                    "model_failure": (analysis.quality_report or {}).get("model_failure"),
                    "blockers": (analysis.quality_report or {}).get("blockers", []),
                },
                created_at=datetime.now(timezone.utc),
            )
        )


def _fallback_template(event: Event) -> ImpactAnalysisOutput:
    """LLM 不可用时，按事件类型返回规则模板输出。"""
    if event.event_type == "macro_policy":
        return _macro_policy_fallback(event)
    return _generic_fallback(event)


def _legacy_graph(analysis: ImpactAnalysis) -> dict:
    nodes: list[dict] = [
        {
            "node_id": "node_event",
            "node_type": "event",
            "label": analysis.event_title_snapshot,
            "layer": 0,
        }
    ]
    edges: list[dict] = []
    chain_endpoints: dict[str, str] = {}
    for chain_index, chain in enumerate(analysis.transmission_chains):
        previous = "node_event"
        for step_index, step in enumerate(chain.get("steps", [])):
            node_id = f"node_legacy_{chain_index}_{step_index}"
            nodes.append(
                {
                    "node_id": node_id,
                    "node_type": "mechanism" if step_index == 0 else "variable",
                    "label": step.get("description", ""),
                    "layer": step_index + 1,
                    "group": chain.get("chain_id"),
                }
            )
            edges.append(
                {
                    "edge_id": f"edge_legacy_{chain_index}_{step_index}",
                    "source_node_id": previous,
                    "target_node_id": node_id,
                    "mechanism": chain.get("mechanism", "legacy transmission"),
                    "direction": "uncertain",
                    "order": "direct" if step_index == 0 else "first_order",
                    "horizon": "unknown",
                    "inference_kind": "inference",
                    "confidence": chain.get("confidence", 0.0),
                    "evidence_refs": [],
                }
            )
            previous = node_id
        chain_endpoints[chain.get("chain_id", f"legacy_{chain_index}")] = previous

    horizon_map = {"short": "2_5d", "medium": "1_4q", "long": "1y_plus"}
    for impact_index, impact in enumerate(analysis.impacts):
        node_id = f"node_legacy_impact_{impact_index}"
        nodes.append(
            {
                "node_id": node_id,
                "node_type": "impact",
                "label": impact.get("target_name", "未命名影响对象"),
                "layer": 4,
                "group": impact.get("target_type"),
            }
        )
        refs = [ref for ref in impact.get("chain_refs", []) if ref in chain_endpoints]
        sources = [chain_endpoints[ref] for ref in refs] or ["node_event"]
        for source_index, source_id in enumerate(sources):
            edges.append(
                {
                    "edge_id": f"edge_legacy_impact_{impact_index}_{source_index}",
                    "source_node_id": source_id,
                    "target_node_id": node_id,
                    "mechanism": (
                        impact.get("rationale", "兼容影响映射")
                        if refs
                        else "兼容影响映射（未关联传导链）"
                    ),
                    "direction": impact.get("direction", "uncertain"),
                    "order": "first_order" if refs else "direct",
                    "horizon": horizon_map.get(impact.get("horizon"), "unknown"),
                    "inference_kind": "inference",
                    "confidence": impact.get("confidence", 0.0),
                    "evidence_refs": [
                        {"evidence_type": "claim", "evidence_id": claim_id, "stance": "supports"}
                        for claim_id in impact.get("claim_ids", [])
                    ],
                }
            )
    return {"nodes": nodes, "edges": edges}


def _legacy_projection(output: ImpactAnalysisOutputV2) -> tuple[list[dict], list[dict]]:
    """将 V2 因果图投影为当前 API/前端仍使用的链和目标字段。"""
    nodes = {node.node_id: node for node in output.causal_graph.nodes}
    chains: list[dict] = []
    for scenario in output.scenarios:
        edges = [
            edge for edge in output.causal_graph.edges if edge.edge_id in scenario.active_edge_ids
        ]
        if not edges:
            continue
        chain_id = f"chn_v2_{scenario.scenario_id.removeprefix('scn_')}"
        chains.append(
            TransmissionChain(
                chain_id=chain_id,
                mechanism=f"{scenario.name} 情景传导",
                steps=[
                    {
                        "step": index,
                        "description": (
                            f"{nodes[edge.source_node_id].label} → "
                            f"{nodes[edge.target_node_id].label}：{edge.mechanism}"
                        ),
                    }
                    for index, edge in enumerate(edges)
                    if edge.source_node_id in nodes and edge.target_node_id in nodes
                ],
                confidence=(sum(edge.confidence for edge in edges) / len(edges) if edges else 0.0),
            ).model_dump()
        )

    impacts: list[dict] = []
    for assessment in output.impact_assessments:
        direction = "neutral"
        magnitude = "uncertain"
        if assessment.dimensions:
            direction = assessment.dimensions[0].direction
            magnitude = assessment.dimensions[0].magnitude
        impacts.append(
            ImpactTarget(
                target_type=assessment.target_type,
                target_name=assessment.target_name,
                target_code=assessment.target_code,
                direction=direction,
                magnitude=magnitude,
                horizon={
                    "0_1d": "short",
                    "2_5d": "short",
                    "1_4w": "medium",
                    "1_4q": "medium",
                    "1y_plus": "long",
                }.get(assessment.horizon, "uncertain"),
                confidence=assessment.confidence,
                rationale="；".join(assessment.exposure_path),
                chain_refs=[
                    f"chn_v2_{scenario.scenario_id.removeprefix('scn_')}"
                    for scenario in output.scenarios
                    if scenario.scenario_id == assessment.scenario_id
                ],
                claim_ids=[
                    evidence.evidence_id
                    for evidence in assessment.evidence_refs
                    if evidence.evidence_type == "claim"
                ],
            ).model_dump()
        )
    return chains, impacts


def _macro_policy_fallback(event: Event) -> ImpactAnalysisOutput:
    key_fields = event.key_fields or {}
    rate_decision = key_fields.get("rate_decision", "unknown")
    policy_body = key_fields.get("policy_body", "central_bank")

    if rate_decision == "hike":
        summary = (
            f"{policy_body} 加息通常收紧流动性、推升无风险利率，"
            "对高估值成长股和高杠杆板块偏负面，对银行净息差偏正面。"
        )
        impacts = [
            ImpactTarget(
                target_type="sector",
                target_name="银行",
                direction="positive",
                magnitude="moderate",
                horizon="medium",
                confidence=0.65,
                rationale="加息往往扩大净息差，利好银行利息收入；但需关注资产质量恶化风险。",
            ),
            ImpactTarget(
                target_type="sector",
                target_name="房地产",
                direction="negative",
                magnitude="strong",
                horizon="medium",
                confidence=0.70,
                rationale="融资成本上升直接压制高杠杆地产开发商和按揭需求。",
            ),
            ImpactTarget(
                target_type="asset_class",
                target_name="成长股/高估值科技",
                direction="negative",
                magnitude="moderate",
                horizon="short",
                confidence=0.60,
                rationale="折现率上升压缩远期现金流估值。",
            ),
            ImpactTarget(
                target_type="macro_variable",
                target_name="汇率",
                direction="mixed",
                magnitude="moderate",
                horizon="short",
                confidence=0.55,
                rationale="利差扩大可能推升本币汇率，但取决于市场预期与资本流动。",
            ),
        ]
        steps = [
            {"step": 0, "description": f"{policy_body} 宣布加息"},
            {"step": 1, "description": "市场无风险利率与融资成本上升"},
            {"step": 2, "description": "高杠杆、远期现金流资产估值承压；银行息差受益"},
        ]
    elif rate_decision == "cut":
        summary = (
            f"{policy_body} 降息通常释放流动性、降低融资成本，"
            "利好风险资产和高杠杆板块，对银行净息差偏负面。"
        )
        impacts = [
            ImpactTarget(
                target_type="sector",
                target_name="房地产",
                direction="positive",
                magnitude="moderate",
                horizon="medium",
                confidence=0.65,
                rationale="融资成本下降刺激按揭和开发融资需求。",
            ),
            ImpactTarget(
                target_type="asset_class",
                target_name="成长股/高估值科技",
                direction="positive",
                magnitude="moderate",
                horizon="short",
                confidence=0.60,
                rationale="折现率下降提升远期现金流估值。",
            ),
            ImpactTarget(
                target_type="sector",
                target_name="银行",
                direction="negative",
                magnitude="moderate",
                horizon="medium",
                confidence=0.55,
                rationale="净息差可能收窄。",
            ),
        ]
        steps = [
            {"step": 0, "description": f"{policy_body} 宣布降息"},
            {"step": 1, "description": "市场利率与融资成本下降"},
            {"step": 2, "description": "风险资产估值修复，高杠杆行业受益"},
        ]
    else:
        summary = f"{policy_body} 维持利率不变，市场影响取决于政策声明措辞与预期差。"
        impacts = [
            ImpactTarget(
                target_type="market",
                target_name="整体市场",
                direction="neutral",
                magnitude="weak",
                horizon="short",
                confidence=0.50,
                rationale="利率维持不变通常不会直接改变资产定价，但政策沟通可能引发波动。",
            )
        ]
        steps = [
            {"step": 0, "description": f"{policy_body} 宣布维持利率不变"},
            {"step": 1, "description": "政策声明释放未来指引信号"},
            {"step": 2, "description": "资产价格按预期差调整"},
        ]

    return ImpactAnalysisOutput(
        summary=summary,
        transmission_chains=[
            TransmissionChain(
                chain_id="chn_fallback_rate",
                mechanism="利率传导",
                steps=steps,
                confidence=0.55,
            )
        ],
        impacts=impacts,
        macro_assumptions=[f"{policy_body} 政策信号被市场充分定价"],
        watch_items=["后续货币政策表态", "通胀与就业数据", "跨境资本流动"],
        confidence=0.55,
    )


def _generic_fallback(event: Event) -> ImpactAnalysisOutput:
    return ImpactAnalysisOutput(
        summary=f"事件 {event.title} 的影响分析暂不可用，待 LLM 配置就绪后重新生成。",
        transmission_chains=[],
        impacts=[
            ImpactTarget(
                target_type="market",
                target_name="相关市场",
                direction="mixed",
                magnitude="uncertain",
                horizon="uncertain",
                confidence=0.30,
                rationale="无足够结构化信息生成确定性影响。",
            )
        ],
        macro_assumptions=["事件影响需结合后续信息披露评估"],
        watch_items=["事件后续进展", "相关实体公告", "市场情绪指标"],
        confidence=0.30,
    )
