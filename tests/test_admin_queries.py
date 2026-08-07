from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.domain import Event, FactCard, User
from app.main import create_app
from app.platform.repository import InMemoryRepository, SqlAlchemyRepository

SCHEMA_MAP = {
    "ingestion": None,
    "events": None,
    "evidence": None,
    "publishing": None,
    "platform": None,
    "analysis": None,
}
NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def _card(
    report_id: str,
    event_id: str,
    version: int,
    status: str,
    as_of: datetime,
) -> FactCard:
    return FactCard(
        id=report_id,
        event_id=event_id,
        version=version,
        status=status,
        title=report_id,
        summary=report_id,
        claim_ids=[],
        as_of=as_of,
    )


def _save_cards(repository, cards: list[FactCard]) -> None:
    with repository.transaction() as transaction:
        for card in cards:
            transaction.save_fact_card(card)


def _headers(client: TestClient, role: str) -> dict[str, str]:
    user = User(
        id=f"usr-{role}",
        username=role,
        password_hash="unused",
        role=role,
    )
    client.app.state.repository.save_user(user)
    token = client.app.state.token_manager.issue(user)
    return {"Authorization": f"Bearer {token}"}


def test_event_response_exposes_classification_fields() -> None:
    application = create_app()
    with TestClient(application) as client:
        client.app.state.repository.save_event(
            Event(
                id="evt-fields",
                event_type="earnings_guidance",
                status="active",
                title="Guidance",
                entity_ids=["000001.SZ"],
                document_ids=["doc-1"],
                importance=0.8,
                urgency="high",
                occurred_at=NOW,
                confidence=0.91,
                key_fields={"growth": "20%"},
                missing_required=["period"],
            )
        )

        response = client.get(
            "/api/v1/events/evt-fields",
            headers=_headers(client, "researcher"),
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["confidence"] == 0.91
    assert data["key_fields"] == {"growth": "20%"}
    assert data["missing_required"] == ["period"]


@pytest.mark.parametrize("role", ["researcher", "reviewer", "publisher", "admin"])
def test_report_list_allows_admin_query_roles(role: str) -> None:
    application = create_app()
    with TestClient(application) as client:
        response = client.get("/api/v1/reports", headers=_headers(client, role))

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_report_list_requires_authentication_and_supports_filters_and_limit() -> None:
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        _save_cards(
            repository,
            [
                _card("rpt-old", "evt-a", 1, "published", NOW),
                _card("rpt-v2", "evt-a", 2, "approved", NOW),
                _card("rpt-new", "evt-b", 1, "published", NOW + timedelta(hours=1)),
            ],
        )

        assert client.get("/api/v1/reports").status_code == 401
        headers = _headers(client, "admin")
        filtered = client.get(
            "/api/v1/reports",
            params={"event_id": "evt-a", "status_filter": "approved"},
            headers=headers,
        )
        limited = client.get("/api/v1/reports", params={"limit": 1}, headers=headers)
        too_large = client.get("/api/v1/reports", params={"limit": 501}, headers=headers)

    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()["data"]] == ["rpt-v2"]
    assert limited.status_code == 200
    assert [item["id"] for item in limited.json()["data"]] == ["rpt-new"]
    assert too_large.status_code == 422


@pytest.mark.parametrize("adapter", ["memory", "sqlalchemy"])
def test_repository_report_query_is_consistent(adapter: str, tmp_path) -> None:
    if adapter == "memory":
        repository = InMemoryRepository()
    else:
        repository = SqlAlchemyRepository(
            f"sqlite:///{tmp_path / 'reports.db'}",
            schema_translate_map=SCHEMA_MAP,
        )
        repository.create_schema_for_tests()
    _save_cards(
        repository,
        [
            _card("rpt-old", "evt-a", 1, "published", NOW),
            _card("rpt-v2", "evt-a", 2, "approved", NOW),
            _card("rpt-new", "evt-b", 1, "published", NOW + timedelta(hours=1)),
        ],
    )

    assert [card.id for card in repository.list_fact_cards()] == [
        "rpt-new",
        "rpt-v2",
        "rpt-old",
    ]
    assert [card.id for card in repository.list_fact_cards(event_id="evt-a")] == [
        "rpt-v2",
        "rpt-old",
    ]
    assert [card.id for card in repository.list_fact_cards(status="published", limit=1)] == [
        "rpt-new"
    ]
