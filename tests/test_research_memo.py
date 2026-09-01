from datetime import datetime, timezone

from app.domain import Claim, Event, WorkflowRun
from app.platform.repository import InMemoryRepository
from app.publishing.assembler import ReportAssembler
from app.publishing.service import FactCardService


def _event() -> Event:
    return Event(
        id="evt_memo",
        event_type="macro_policy",
        status="triaged",
        title="工业企业利润数据发布",
        entity_ids=[],
        document_ids=["doc_memo"],
        importance=0.8,
        urgency="normal",
        occurred_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def _claim() -> Claim:
    return Claim(
        id="clm_memo",
        event_id="evt_memo",
        subject_text="规模以上工业企业",
        predicate="利润同比增长",
        object_value={"value": "18.7%"},
        status="verified",
        confidence=0.9,
        evidence_ids=["evd_memo"],
        as_of=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def test_research_memo_separates_reader_prose_from_research_pack() -> None:
    repository = InMemoryRepository()
    event = _event()
    repository.save_event(event)
    repository.save_claim(_claim())
    run = WorkflowRun(
        id="wfr_memo",
        event_id=event.id,
        trigger_id="manual",
        status="running",
        as_of=datetime(2026, 9, 1, tzinfo=timezone.utc),
        blackboard={
            "research_memo": {
                "conclusion": "利润数据改善，但市场定价仍需验证。",
                "direction": "mixed",
                "horizon": "short_term",
                "confidence": 0.6,
                "sections": [
                    {
                        "kind": "why_now",
                        "title": "为何值得关注",
                        "body": "工业利润同比增长 18.7%。",
                        "claim_ids": ["clm_memo"],
                        "card_refs": [],
                    },
                    {
                        "kind": "evidence",
                        "title": "重复事实",
                        "body": "工业利润同比增长 18.7%。",
                        "claim_ids": ["clm_memo"],
                        "card_refs": [],
                    },
                ],
            },
            "research_pack": {"fact_cards": [{"claim_id": "clm_memo"}]},
            "company_analysis": {"model_run_id": "mlr_company"},
            "counter_analysis": {"model_run_id": "mlr_counter"},
            "synthesis": {"model_run_id": "mlr_synthesis"},
        },
    )

    draft = ReportAssembler(repository).assemble(run, event)

    assert draft["report_type"] == "research_memo"
    assert len(draft["content"]["memo"]["sections"]) == 1
    assert draft["content"]["memo"]["sections"][0]["citation_ids"] == ["E1"]
    assert draft["content"]["research_pack"]["fact_cards"] == [{"claim_id": "clm_memo"}]
    assert draft["provenance"]["semantic_fingerprint"]


def test_same_semantic_memo_does_not_create_another_report_version() -> None:
    repository = InMemoryRepository()
    event = _event()
    repository.save_event(event)
    run = WorkflowRun(
        id="wfr_memo",
        event_id=event.id,
        trigger_id="manual",
        status="running",
        as_of=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    draft = {
        "title": event.title,
        "summary": "同一研究结论",
        "report_type": "research_memo",
        "content": {"memo": {"conclusion": "同一研究结论"}},
        "provenance": {"semantic_fingerprint": "stable-fingerprint"},
    }

    first = FactCardService(repository).create_from_draft(event, run, draft)
    replay = FactCardService(repository).create_from_draft(event, run, draft)

    assert replay.id == first.id
    assert len(repository.list_fact_cards_for_event(event.id)) == 1
