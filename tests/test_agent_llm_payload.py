"""Agent 节点应消费 LLM response.payload，解析失败时回退确定性输出。"""

from datetime import datetime, timezone

from app.domain import Claim, Event
from app.model_gateway.service import ModelGateway, ModelResponse
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository
from app.research.tools.gateway import ToolGateway
from app.workflows.agents import CompanyAnalystAgent, SkepticAgent, SynthesizerAgent


def _fake_tool_gateway() -> ToolGateway:
    class FakeToolGateway(ToolGateway):
        def __init__(self) -> None:  # noqa: D107
            pass

        def invoke(self, **kwargs):  # noqa: D102
            return {"items": []}

    return FakeToolGateway()  # type: ignore[return-value]


def _event() -> Event:
    return Event(
        id="evt-agent",
        event_type="earnings_guidance",
        status="triaged",
        title="业绩预告",
        entity_ids=["000001.SZ"],
        document_ids=["doc_1"],
        importance=0.8,
        urgency="normal",
        occurred_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        key_fields={"period": "2026-H1", "change_rate": {"min": 20, "max": 30}},
    )


def _claim(event_id: str) -> Claim:
    return Claim(
        id=new_id("clm"),
        event_id=event_id,
        subject_text="000001.SZ",
        predicate="document_discloses_event",
        object_value={"type": "string", "value": "earnings_guidance"},
        status="verified",
        confidence=0.9,
        evidence_ids=[new_id("evd")],
        as_of=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )


def _fake_gateway(payload: dict | None) -> ModelGateway:
    class FakeGateway(ModelGateway):
        def __init__(self) -> None:  # noqa: D107
            pass

        def invoke(self, request):  # noqa: D102
            return ModelResponse(run_id=new_id("mdr"), payload=payload or {})

    return FakeGateway()  # type: ignore[return-value]


def test_company_analyst_uses_payload_when_valid() -> None:
    repository = InMemoryRepository()
    repository.save_event(_event())
    payload = {
        "status": "complete",
        "direction": "negative",
        "impact_horizon": "medium_term",
        "confidence": 0.8,
        "scenarios": [
            {"name": "bear", "outcome": "下滑", "probability_label": "high"}
        ],
        "assumptions": [
            {"assumption_id": "asm_x", "statement": "假设", "importance": "medium"}
        ],
    }
    agent = CompanyAnalystAgent(_fake_gateway(payload), _fake_tool_gateway())  # type: ignore[arg-type]
    state = {
        "event_id": "evt-agent",
        "as_of": "2026-07-21T00:00:00+00:00",
        "fact_check_snapshot": {"verified_claim_ids": ["clm_1"]},
    }
    result = agent.run(state, repository)
    assert result["company_analysis"]["direction"] == "negative"
    assert result["company_analysis"]["impact_horizon"] == "medium_term"


def test_company_analyst_falls_back_on_invalid_payload() -> None:
    repository = InMemoryRepository()
    repository.save_event(_event())
    agent = CompanyAnalystAgent(
        _fake_gateway({"invalid": "data"}), _fake_tool_gateway()  # type: ignore[arg-type]
    )
    state = {
        "event_id": "evt-agent",
        "as_of": "2026-07-21T00:00:00+00:00",
        "fact_check_snapshot": {"verified_claim_ids": ["clm_1"]},
    }
    result = agent.run(state, repository)
    # 事件类型 earnings_guidance 的确定性方向是 positive
    assert result["company_analysis"]["direction"] == "positive"


def test_skeptic_uses_payload_when_valid() -> None:
    payload = {
        "status": "complete",
        "review_required": True,
        "direction_assessment": "reverses",
        "recommended_confidence": 0.3,
        "counter_arguments": [
            {
                "argument_id": "ctr_x",
                "statement": "反证",
                "severity": "high",
            }
        ],
    }
    agent = SkepticAgent(_fake_gateway(payload))
    state = {
        "event_id": "evt-agent",
        "as_of": "2026-07-21T00:00:00+00:00",
        "company_analysis": {
            "financial_impacts": [{"claim_ids": ["clm_1"]}],
            "direction": "positive",
        },
    }
    result = agent.run(state, None)  # type: ignore[arg-type]
    assert result["counter_analysis"]["direction_assessment"] == "reverses"
    assert result["counter_analysis"]["review_required"] is True


def test_skeptic_falls_back_on_invalid_payload() -> None:
    agent = SkepticAgent(_fake_gateway({"review_required": "not_a_bool"}))
    state = {
        "event_id": "evt-agent",
        "as_of": "2026-07-21T00:00:00+00:00",
        "company_analysis": {
            "financial_impacts": [{"claim_ids": ["clm_1"]}],
            "direction": "positive",
        },
    }
    result = agent.run(state, None)  # type: ignore[arg-type]
    assert result["counter_analysis"]["direction_assessment"] == "weakens"


def test_synthesizer_uses_payload_when_valid() -> None:
    payload = {
        "status": "complete",
        "summary": "模型返回的合成摘要",
        "signal": "strongly_negative",
        "confidence": 0.2,
        "horizon": "short_term",
        "key_fact_claim_ids": ["clm_1"],
    }
    agent = SynthesizerAgent(_fake_gateway(payload))
    state = {
        "event_id": "evt-agent",
        "as_of": "2026-07-21T00:00:00+00:00",
        "fact_check_snapshot": {"verified_claim_ids": ["clm_1"]},
        "company_analysis": {"direction": "positive", "confidence": 0.8},
        "counter_analysis": {"recommended_confidence": 0.5},
    }
    result = agent.run(state, None)  # type: ignore[arg-type]
    assert result["synthesis"]["summary"] == "模型返回的合成摘要"
    assert result["synthesis"]["signal"] == "strongly_negative"


def test_synthesizer_falls_back_on_invalid_payload() -> None:
    agent = SynthesizerAgent(_fake_gateway({"signal": "invalid_signal"}))
    state = {
        "event_id": "evt-agent",
        "as_of": "2026-07-21T00:00:00+00:00",
        "fact_check_snapshot": {"verified_claim_ids": ["clm_1"]},
        "company_analysis": {"direction": "positive", "confidence": 0.8},
        "counter_analysis": {"recommended_confidence": 0.5},
    }
    result = agent.run(state, None)  # type: ignore[arg-type]
    assert result["synthesis"]["signal"] == "moderately_positive"
