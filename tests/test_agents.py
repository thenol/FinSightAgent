from datetime import datetime, timezone

from app.domain import Claim, Event
from app.model_gateway.service import ModelGateway
from app.platform.repository import InMemoryRepository
from app.research.tools.gateway import ToolGateway
from app.workflows.agents import (
    CompanyAnalystAgent,
    FactCheckerAgent,
    SkepticAgent,
    SynthesizerAgent,
)
from app.workflows.schemas import (
    CompanyAnalysisOutput,
    SkepticOutput,
    SynthesisOutput,
)

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def make_event() -> Event:
    return Event(
        id="evt_agent",
        event_type="earnings_guidance",
        status="triaged",
        title="示例公司业绩预告",
        entity_ids=["000001.SZ"],
        document_ids=["doc_1"],
        importance=0.8,
        urgency="normal",
        occurred_at=NOW,
        key_fields={
            "period": "2026-H1",
            "change_rate": {"min": "20", "max": "30", "unit": "percent"},
        },
    )


def state_with(verified_claim_ids: list[str]) -> dict:
    return {
        "event_id": "evt_agent",
        "workflow_id": "wfr_1",
        "as_of": NOW.isoformat(),
        "fact_check_snapshot": {"verified_claim_ids": verified_claim_ids},
    }


def test_fact_checker_output_structure() -> None:
    repository = InMemoryRepository()
    event = make_event()
    repository.save_event(event)
    repository.save_claim(
        Claim(
            id="clm_1",
            event_id="evt_agent",
            subject_text="000001.SZ",
            predicate="document_discloses_event",
            object_value={"type": "string", "value": "earnings_guidance"},
            status="verified",
            confidence=0.9,
            evidence_ids=[],
            as_of=NOW,
        )
    )
    agent = FactCheckerAgent(ModelGateway(repository))

    result = agent.run(
        {"event_id": "evt_agent", "as_of": NOW.isoformat()}, repository, ToolGateway(repository)
    )
    snapshot = result["fact_check_snapshot"]
    assert "claim_ids" in snapshot
    assert snapshot["verified_claim_ids"] == ["clm_1"]


def test_company_analyst_output_passes_schema() -> None:
    repository = InMemoryRepository()
    repository.save_event(make_event())
    agent = CompanyAnalystAgent(ModelGateway(repository), ToolGateway(repository))

    result = agent.run(state_with(["clm_1"]), repository)
    output = CompanyAnalysisOutput.model_validate(result["company_analysis"])
    assert output.schema_version == "1.0.0"
    assert output.direction == "positive"
    assert output.scenarios  # 至少一个情景
    assert output.financial_impacts[0].claim_ids == ["clm_1"]
    # 数值计算走了工具
    assert output.financial_impacts[0].tool_result_ids


def test_company_analyst_partial_when_no_verified_claims() -> None:
    repository = InMemoryRepository()
    repository.save_event(make_event())
    agent = CompanyAnalystAgent(ModelGateway(repository), ToolGateway(repository))

    result = agent.run(state_with([]), repository)
    output = CompanyAnalysisOutput.model_validate(result["company_analysis"])
    assert output.status == "partial"
    assert output.confidence < 0.5


def test_skeptic_output_passes_schema() -> None:
    repository = InMemoryRepository()
    repository.save_event(make_event())
    company_state = {
        **state_with(["clm_1"]),
        "company_analysis": {
            "direction": "positive",
            "confidence": 0.65,
            "financial_impacts": [{"claim_ids": ["clm_1"], "basis": "公告"}],
        },
    }
    agent = SkepticAgent(ModelGateway(repository))

    result = agent.run(company_state, repository)
    output = SkepticOutput.model_validate(result["counter_analysis"])
    assert output.schema_version == "1.0.0"
    assert output.counter_arguments
    assert output.thesis_breakers
    assert output.direction_assessment == "weakens"
    assert output.recommended_confidence < 0.65


def test_synthesizer_output_passes_schema_and_only_uses_existing_results() -> None:
    repository = InMemoryRepository()
    repository.save_event(make_event())
    state = {
        **state_with(["clm_1"]),
        "company_analysis": {
            "direction": "positive",
            "confidence": 0.65,
            "impact_horizon": "short_term",
            "financial_impacts": [{"basis": "公告业绩预告", "claim_ids": ["clm_1"]}],
        },
        "counter_analysis": {
            "direction_assessment": "weakens",
            "recommended_confidence": 0.55,
            "counter_arguments": [{"statement": "股价可能已定价"}],
        },
    }
    agent = SynthesizerAgent(ModelGateway(repository))

    result = agent.run(state, repository)
    output = SynthesisOutput.model_validate(result["synthesis"])
    assert output.schema_version == "1.0.0"
    assert output.key_fact_claim_ids == ["clm_1"]
    assert output.supporting_points
    assert output.watch_items
    assert output.reanalysis_triggers
    # Synthesis 引用的关键事实必须来自已验证 Claim，不得新增
    assert all(cid in ["clm_1"] for cid in output.key_fact_claim_ids)
