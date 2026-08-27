from datetime import datetime, timezone

from app.analysis.preliminary import PreliminaryAssessmentService
from app.domain import Event
from app.platform.repository import InMemoryRepository


def _event(repo: InMemoryRepository) -> Event:
    event = Event(
        id="evt_preliminary",
        event_type="macro_policy",
        status="triaged",
        title="央行调整政策利率",
        entity_ids=[],
        document_ids=[],
        importance=0.8,
        urgency="high",
        occurred_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        key_fields={"rate_change_bp": "25"},
        missing_required=[],
    )
    repo.save_event(event)
    return event


def test_preliminary_assessment_is_persisted_as_limited_reference() -> None:
    repo = InMemoryRepository()
    event = _event(repo)
    assessment = PreliminaryAssessmentService(repo).generate(event.id)

    assert assessment.status == "limited"
    assert assessment.direction == "uncertain"
    assert assessment.assessment_payload["schema_version"] == "1.0.0"
    assert repo.get_latest_preliminary_assessment_for_event(event.id).id == assessment.id


def test_preliminary_assessment_generation_is_idempotent_for_same_input() -> None:
    repo = InMemoryRepository()
    event = _event(repo)
    service = PreliminaryAssessmentService(repo)

    first = service.generate(event.id)
    second = service.generate(event.id)

    assert second.id == first.id
    assert len(repo.list_preliminary_assessments_for_event(event.id)) == 1
