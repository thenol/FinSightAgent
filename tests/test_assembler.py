from datetime import datetime, timezone

from app.domain import Claim, Event, WorkflowRun
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository
from app.publishing.assembler import REPORT_SCHEMA_VERSION, ReportAssembler


def make_run_and_event(blackboard: dict) -> tuple[WorkflowRun, Event]:
    event = Event(
        id="evt_asm",
        event_type="earnings_guidance",
        status="triaged",
        title="示例公司业绩预告",
        entity_ids=["000001.SZ"],
        document_ids=["doc_1"],
        importance=0.8,
        urgency="normal",
        occurred_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        key_fields={"period": "2026-H1"},
    )
    run = WorkflowRun(
        id="wfr_asm",
        event_id="evt_asm",
        trigger_id="manual",
        status="succeeded",
        as_of=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        blackboard=blackboard,
    )
    return run, event


def test_assembler_produces_research_card_from_full_blackboard() -> None:
    repository = InMemoryRepository()
    run, event = make_run_and_event(
        {
            "company_analysis": {
                "direction": "positive",
                "confidence": 0.65,
                "financial_impacts": [{"basis": "公告"}],
            },
            "counter_analysis": {
                "recommended_confidence": 0.55,
                "counter_arguments": [{"statement": "已定价"}],
            },
            "synthesis": {
                "signal": "moderately_positive",
                "confidence": 0.60,
                "summary": "超预期但部分已定价",
                "watch_items": [{"indicator": "毛利率", "reason": "判断持续性", "horizon": "1q"}],
                "reanalysis_triggers": [
                    {
                        "trigger_type": "new_filing",
                        "condition": "修正公告",
                        "affected_nodes": ["fact_check"],
                    }
                ],
                "limitations": ["未接入市场预期"],
            },
        }
    )
    repository.save_event(event)
    repository.save_claim(
        Claim(
            id=new_id("clm"),
            event_id="evt_asm",
            subject_text="000001.SZ",
            predicate="document_discloses_event",
            object_value={"type": "string", "value": "earnings_guidance"},
            status="verified",
            confidence=0.9,
            evidence_ids=["evd_1"],
            as_of=datetime(2026, 7, 12, tzinfo=timezone.utc),
        )
    )
    draft = ReportAssembler(repository).assemble(run, event)

    assert draft["schema_version"] == REPORT_SCHEMA_VERSION
    assert draft["report_type"] == "research_card"
    assert draft["signal"] == "moderately_positive"
    assert draft["as_of"] == run.as_of.isoformat()
    assert draft["disclaimer"]
    kinds = {s["kind"] for s in draft["sections"]}
    assert "verified_facts" in kinds
    assert "impact" in kinds
    assert "counter_arguments" in kinds
    assert "watch_items" in kinds
    assert "triggers" in kinds


def test_assembler_downgrades_to_fact_card_when_synthesis_fact_only() -> None:
    repository = InMemoryRepository()
    run, event = make_run_and_event(
        {
            "synthesis": {"status": "fact_only", "confidence": 0.4, "summary": "仅事实"},
        }
    )
    repository.save_event(event)
    repository.save_claim(
        Claim(
            id=new_id("clm"),
            event_id="evt_asm",
            subject_text="000001.SZ",
            predicate="document_discloses_event",
            object_value={},
            status="verified",
            confidence=0.9,
            evidence_ids=["evd_1"],
            as_of=datetime(2026, 7, 12, tzinfo=timezone.utc),
        )
    )
    draft = ReportAssembler(repository).assemble(run, event)

    assert draft["report_type"] == "fact_card"
    assert draft["signal"] is None


def test_assembler_partitions_conflicted_and_unverified_claims() -> None:
    repository = InMemoryRepository()
    run, event = make_run_and_event({"synthesis": {"status": "fact_only", "confidence": 0.4}})
    repository.save_event(event)
    for status, eid in [("verified", "evd_v"), ("conflicted", "evd_c"), ("unverified", "evd_u")]:
        repository.save_claim(
            Claim(
                id=new_id("clm"),
                event_id="evt_asm",
                subject_text="000001.SZ",
                predicate="document_discloses_event",
                object_value={},
                status=status,
                confidence=0.5,
                evidence_ids=[eid],
                as_of=datetime(2026, 7, 12, tzinfo=timezone.utc),
            )
        )
    draft = ReportAssembler(repository).assemble(run, event)
    kinds = {s["kind"]: s["items"] for s in draft["sections"]}
    assert kinds["verified_facts"]
    assert kinds["conflicts"]
    assert kinds["unverified"]
