"""事件类型注册表治理（DD-21 §2.4）。"""

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.domain import Document, Event, EventTypeRegistryEntry, FactCard
from app.events.classifier import ClassificationResult
from app.events.schemas import is_candidate_event_type
from app.events.service import EventService
from app.events.type_registry import EventTypeRegistryService
from app.main import create_app
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository
from app.platform.settings import Settings
from app.publishing.briefs import BriefService


def _open_class_event(
    repository: InMemoryRepository,
    *,
    event_type: str = "weather_event",
    suffix: str = "1",
    classification: ClassificationResult | None = None,
) -> Event:
    now = datetime.now(timezone.utc)
    document = Document(
        id=new_id("doc"),
        source_id="src_open",
        source_tier="S",
        external_id=f"open-{suffix}",
        canonical_url=f"https://example.test/open-{suffix}",
        title=f"开放分类样本 {suffix}",
        content=f"独立文档 {suffix}：高温与电网负荷变化。",
        content_hash=f"hash-{suffix}",
        published_at=now,
        ingested_at=now,
    )
    result = classification or ClassificationResult(
        event_type=event_type,
        importance=0.8,
        confidence=0.75,
        missing_required=["candidate_type_confirmation"],
        schema_version="event-router-v2-candidate",
    )
    return EventService(repository).create_event(document, classification=result)


def test_is_candidate_event_type_respects_registry_status() -> None:
    assert is_candidate_event_type("weather_event") is True
    assert is_candidate_event_type("weather_event", registry_status="candidate") is True
    assert is_candidate_event_type("weather_event", registry_status="accepted") is False
    assert is_candidate_event_type("weather_event", registry_status="rejected") is False
    assert is_candidate_event_type("earnings_guidance") is False


def test_first_candidate_creates_registry_and_forces_review() -> None:
    repository = InMemoryRepository()
    event = _open_class_event(repository)

    entry = repository.get_event_type_registry("weather_event")
    assert event.status == "needs_review"
    assert "candidate_type_confirmation" in event.missing_required
    assert entry is not None
    assert entry.status == "candidate"
    assert entry.event_count == 1


def test_candidate_count_increments_on_new_events() -> None:
    repository = InMemoryRepository()
    _open_class_event(repository, suffix="c1")
    _open_class_event(repository, suffix="c2")

    entry = repository.get_event_type_registry("weather_event")
    assert entry is not None
    assert entry.event_count == 2


def test_accept_drops_forced_review_for_later_events() -> None:
    repository = InMemoryRepository()
    _open_class_event(repository, suffix="before")
    EventTypeRegistryService(repository).accept("weather_event", "usr_reviewer")

    later = _open_class_event(repository, suffix="after")
    entry = repository.get_event_type_registry("weather_event")

    assert later.status == "triaged"
    assert "candidate_type_confirmation" not in later.missing_required
    assert entry is not None
    assert entry.status == "accepted"
    assert entry.event_count == 2


def test_reject_sends_later_events_to_cold() -> None:
    repository = InMemoryRepository()
    _open_class_event(repository, suffix="before")
    EventTypeRegistryService(repository).reject("weather_event", "usr_reviewer")

    later = _open_class_event(repository, suffix="after")
    triggers = repository.list_watch_triggers(event_id=later.id)

    assert later.status == "cold"
    assert later.event_type == "weather_event"
    assert triggers
    assert all(t.status == "armed" for t in triggers)


def test_promotion_ready_when_count_reaches_threshold() -> None:
    repository = InMemoryRepository()
    service = EventTypeRegistryService(repository, promotion_threshold=2)
    _open_class_event(repository, suffix="one")
    first = repository.get_event_type_registry("weather_event")
    assert first is not None
    assert service.is_promotion_ready(first) is False

    _open_class_event(repository, suffix="two")
    second = repository.get_event_type_registry("weather_event")
    assert second is not None
    assert service.is_promotion_ready(second) is True


def test_brief_includes_accepted_open_type_and_excludes_candidate() -> None:
    repository = InMemoryRepository()
    day = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
    repository.save_event_type_registry(
        EventTypeRegistryEntry(
            type_label="weather_event",
            status="accepted",
            event_count=5,
        )
    )
    repository.save_event(
        Event(
            id="evt_accepted",
            event_type="weather_event",
            status="triaged",
            title="已升格开放类型",
            entity_ids=["000001.SZ"],
            document_ids=[],
            importance=0.8,
            urgency="high",
            occurred_at=day,
        )
    )
    repository.save_fact_card(
        FactCard(
            id=new_id("rpt"),
            event_id="evt_accepted",
            version=1,
            status="published",
            title="已升格报告",
            summary="测试",
            claim_ids=[],
            as_of=day,
        )
    )
    repository.save_event(
        Event(
            id="evt_candidate",
            event_type="supply_shock",
            status="needs_review",
            title="仍为候选",
            entity_ids=["000002.SZ"],
            document_ids=[],
            importance=0.95,
            urgency="high",
            occurred_at=day,
        )
    )
    repository.save_fact_card(
        FactCard(
            id=new_id("rpt"),
            event_id="evt_candidate",
            version=1,
            status="published",
            title="候选报告",
            summary="测试",
            claim_ids=[],
            as_of=day,
        )
    )

    brief = BriefService(repository).generate(date(2026, 7, 12))
    assert [entry.event_id for entry in brief.entries] == ["evt_accepted"]


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_event_type_api_list_accept_reject_and_audit() -> None:
    with TestClient(create_app()) as client:
        headers = _auth_headers(client)
        repository: InMemoryRepository = client.app.state.repository
        _open_class_event(repository, suffix="api-1")
        _open_class_event(repository, suffix="api-2")

        listed = client.get("/api/v1/event-types?status_filter=candidate", headers=headers)
        assert listed.status_code == 200
        rows = listed.json()["data"]
        assert len(rows) == 1
        assert rows[0]["type_label"] == "weather_event"
        assert rows[0]["event_count"] == 2
        assert rows[0]["promotion_ready"] is False

        accepted = client.post("/api/v1/event-types/weather_event/accept", headers=headers)
        assert accepted.status_code == 200
        assert accepted.json()["data"]["status"] == "accepted"

        conflict = client.post("/api/v1/event-types/weather_event/reject", headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "EVENT_TYPE_ALREADY_DECIDED"

        missing = client.post("/api/v1/event-types/not_a_type/accept", headers=headers)
        assert missing.status_code == 404

        logs = client.get("/api/v1/audit-logs", headers=headers)
        actions = {item["action"] for item in logs.json()["data"]}
        assert "event_type.promoted" in actions


def test_event_type_api_reject() -> None:
    with TestClient(create_app()) as client:
        headers = _auth_headers(client)
        repository: InMemoryRepository = client.app.state.repository
        _open_class_event(repository, event_type="supply_shock", suffix="rej-1")

        rejected = client.post("/api/v1/event-types/supply_shock/reject", headers=headers)
        assert rejected.status_code == 200
        assert rejected.json()["data"]["status"] == "rejected"

        later = _open_class_event(repository, event_type="supply_shock", suffix="rej-2")
        assert later.status == "cold"


def test_promotion_threshold_setting_rejects_zero() -> None:
    with pytest.raises(ValueError, match="FINSIGHT_CANDIDATE_TYPE_PROMOTION_THRESHOLD_INVALID"):
        Settings(
            environment="development",
            repository="memory",
            database_url="postgresql+psycopg://user:pass@db/finsight",
            redis_url="redis://redis:6379/0",
            artifact_root=".data/artifacts",
            jwt_secret="a" * 32,
            bootstrap_admin_username="",
            bootstrap_admin_password="",
            candidate_type_promotion_threshold=0,
        ).validate()
