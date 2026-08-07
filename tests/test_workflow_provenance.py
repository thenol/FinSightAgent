from datetime import datetime, timezone

from app.domain import Claim, Event
from app.platform.repository import InMemoryRepository
from app.publishing.service import FactCardService
from app.workflows.service import WorkflowService

AS_OF = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def _seed_event(repository: InMemoryRepository) -> Event:
    from app.domain import EvidenceSpan

    event = Event(
        id="evt_workflow_provenance",
        event_type="earnings_guidance",
        status="triaged",
        title="业绩预告",
        entity_ids=["000001.SZ"],
        document_ids=["doc_1"],
        importance=0.8,
        urgency="normal",
        occurred_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        key_fields={"period": "2026-H1"},
    )
    repository.save_event(event)
    evidence_id = "evd_workflow_provenance"
    repository.save_evidence(
        EvidenceSpan(
            id=evidence_id,
            document_id="doc_1",
            revision_id="rev_1",
            locator={"type": "html", "block_id": "body-p-001", "char_start": 0, "char_end": 10},
            excerpt="公司披露业绩预告",
            excerpt_hash="hash",
            locator_type="html",
            extraction_method="parser",
            extraction_version="html-blocks-v1",
            created_at=AS_OF,
        )
    )
    repository.save_claim(
        Claim(
            id="clm_workflow_provenance",
            event_id=event.id,
            subject_text="000001.SZ",
            predicate="document_discloses_event",
            object_value={"type": "string", "value": "earnings_guidance"},
            status="verified",
            confidence=0.9,
            evidence_ids=[evidence_id],
            as_of=AS_OF,
        )
    )
    return event


def _run_and_persist_report(
    repository: InMemoryRepository, service: WorkflowService, event: Event
):
    created = service.create(event.id, "manual", as_of=AS_OF)
    completed = service.run(created.id)
    assert completed.status == "succeeded"
    draft = completed.blackboard["report_draft"]
    report = FactCardService(repository).create_from_draft(event, completed, draft)
    return completed, report


def test_report_traces_to_workflow_model_runs_and_replay_is_stable() -> None:
    repository = InMemoryRepository()
    event = _seed_event(repository)
    service = WorkflowService(repository)

    first_workflow, first_report = _run_and_persist_report(repository, service, event)
    original_model_run_ids = first_workflow.blackboard["provenance"]["model_run_ids"]

    assert len(original_model_run_ids) == 4
    assert first_report.provenance["workflow_run_id"] == first_workflow.id
    assert first_report.provenance["model_run_ids"] == original_model_run_ids
    assert first_report.provenance["analysis_refs"] == [
        "fact_check_snapshot",
        "company_analysis",
        "counter_analysis",
        "synthesis",
    ]
    traced_workflow = repository.get_workflow_run(first_report.provenance["workflow_run_id"])
    assert traced_workflow is not None
    assert traced_workflow.blackboard["provenance"]["model_run_ids"] == original_model_run_ids
    persisted_runs = {run.id: run for run in repository.list_model_runs()}
    assert set(first_report.provenance["model_run_ids"]) <= persisted_runs.keys()

    replayed_workflow, replayed_report = _run_and_persist_report(repository, service, event)

    assert replayed_workflow.id != first_workflow.id
    assert replayed_workflow.blackboard["provenance"]["model_run_ids"] == original_model_run_ids
    assert replayed_report.provenance["model_run_ids"] == original_model_run_ids
    assert {run.id for run in repository.list_model_runs()} == set(persisted_runs)
    assert "prompt" not in replayed_report.provenance
    assert "input_payload" not in replayed_report.provenance
    assert "output_payload" not in replayed_report.provenance
