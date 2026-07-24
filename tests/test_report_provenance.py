from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.domain import Claim, Event, ToolCall, WorkflowRun
from app.platform.repository import InMemoryRepository, ReportVersionConflict
from app.publishing.assembler import ReportAssembler
from app.publishing.service import FactCardService


def _event() -> Event:
    return Event(
        id="evt_report_provenance",
        event_type="major_contract",
        status="triaged",
        title="重大合同",
        entity_ids=["600000.SH"],
        document_ids=["doc_1"],
        importance=0.8,
        urgency="normal",
        occurred_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )


def _run() -> WorkflowRun:
    return WorkflowRun(
        id="wfr_report_provenance",
        event_id="evt_report_provenance",
        trigger_id="manual",
        status="succeeded",
        as_of=datetime(2026, 7, 21, tzinfo=timezone.utc),
        blackboard={
            "company_analysis": {
                "model_run_id": "mdl_company",
                "financial_impacts": [{"statement": "订单增长"}],
            },
            "counter_analysis": {
                "model_run_id": "mdl_skeptic",
                "counter_arguments": [{"statement": "收入确认仍有不确定性"}],
            },
            "synthesis": {
                "model_run_id": "mdl_synthesis",
                "summary": "基本面改善，但需继续验证。",
                "confidence": 0.62,
                "horizon": "medium_term",
                "watch_items": [{"indicator": "订单"}],
                "reanalysis_triggers": [{"condition": "修正公告"}],
            },
        },
    )


def test_report_snapshot_persists_ac008_content_and_provenance() -> None:
    repository = InMemoryRepository()
    event, run = _event(), _run()
    repository.save_event(event)
    repository.save_claim(
        Claim(
            id="clm_report_provenance",
            event_id=event.id,
            subject_text="600000.SH",
            predicate="document_discloses_event",
            object_value={},
            status="verified",
            confidence=0.9,
            evidence_ids=["evd_1"],
            as_of=run.as_of,
        )
    )
    repository.save_tool_call(
        ToolCall(
            id="tlc_report_provenance",
            workflow_id=run.id,
            agent_type="company",
            tool_name="market_data",
            arguments={}, result={}, as_of=run.as_of, status="succeeded",
        )
    )

    draft = ReportAssembler(repository).assemble(run, event)
    report = FactCardService(repository).create_from_draft(event, run, draft)

    assert report.content == draft["content"]
    assert set(report.content) == {
        "conclusion", "confidence", "time_range", "positive_viewpoints",
        "negative_viewpoints", "watch_items", "reanalysis_conditions",
    }
    assert report.provenance["workflow_run_id"] == run.id
    assert report.provenance["model_run_ids"] == ["mdl_company", "mdl_skeptic", "mdl_synthesis"]
    assert report.provenance["tool_call_ids"] == ["tlc_report_provenance"]


def test_report_versions_are_append_only_and_conflict_protected() -> None:
    repository = InMemoryRepository()
    event, run = _event(), _run()
    repository.save_event(event)
    first = FactCardService(repository).create_from_draft(event, run, {"summary": "初版"})
    replacement = FactCardService(repository).transition(first, "approved", "审核通过")

    assert first.id != replacement.id
    assert replacement.supersedes_report_id == first.id
    with pytest.raises(ReportVersionConflict):
        repository.save_fact_card(replace(replacement, id="rpt_conflict"))
