from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.domain import Event, FactCard, User
from app.main import create_app
from app.platform.pagination import encode_cursor
from app.platform.repository import (
    ApiIdempotencyRecord,
    InMemoryRepository,
    SqlAlchemyRepository,
)

SCHEMA_MAP = {
    "ingestion": None,
    "events": None,
    "evidence": None,
    "publishing": None,
    "platform": None,
    "analysis": None,
}
NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def _headers(client: TestClient, role: str) -> dict[str, str]:
    user = User(
        id=f"usr-{role}",
        username=f"hardening-{role}",
        password_hash="unused",
        role=role,
    )
    client.app.state.repository.save_user(user)
    token = client.app.state.token_manager.issue(user)
    return {"Authorization": f"Bearer {token}"}


def _source_payload(code: str = "idempotent-source") -> dict:
    return {
        "code": code,
        "name": "Idempotent source",
        "trust_tier": "S",
        "feed_url": "https://source.example.test/feed.xml",
        "allowed_domains": ["source.example.test"],
    }


def _event(index: int) -> Event:
    return Event(
        id=f"evt-{index:02d}",
        event_type="earnings_guidance",
        status="active",
        title=f"Event {index}",
        entity_ids=[],
        document_ids=[],
        importance=0.5,
        urgency="normal",
        occurred_at=NOW + timedelta(minutes=index),
    )


def _card(index: int) -> FactCard:
    return FactCard(
        id=f"rpt-{index:02d}",
        event_id=f"evt-{index:02d}",
        version=1,
        status="published",
        title=f"Report {index}",
        summary="summary",
        claim_ids=[],
        as_of=NOW + timedelta(minutes=index),
    )


def _repository(adapter: str, tmp_path):
    if adapter == "memory":
        return InMemoryRepository()
    repository = SqlAlchemyRepository(
        f"sqlite:///{tmp_path / f'{adapter}.db'}",
        schema_translate_map=SCHEMA_MAP,
    )
    repository.create_schema_for_tests()
    return repository


def test_business_api_authentication_and_role_boundaries() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/documents/ingest", json={}).status_code == 401
        assert client.get("/api/v1/events").status_code == 401
        assert client.get("/api/v1/reports/rpt-missing").status_code == 401
        assert client.get("/api/v1/briefs/daily").status_code == 401

        researcher = _headers(client, "researcher")
        forbidden = client.post(
            "/api/v1/sources",
            headers=researcher,
            json=_source_payload("forbidden-source"),
        )
        assert forbidden.status_code == 403


def test_source_write_idempotency_replays_and_rejects_changed_payload() -> None:
    with TestClient(create_app()) as client:
        headers = {
            **_headers(client, "admin"),
            "Idempotency-Key": "source-create-1",
        }
        first = client.post("/api/v1/sources", headers=headers, json=_source_payload())
        replay = client.post("/api/v1/sources", headers=headers, json=_source_payload())
        changed = client.post(
            "/api/v1/sources",
            headers=headers,
            json={**_source_payload(), "name": "Changed"},
        )

        assert first.status_code == replay.status_code == 201
        assert replay.json() == first.json()
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert len(
            [
                source
                for source in client.app.state.repository.list_sources()
                if source.code == "idempotent-source"
            ]
        ) == 1


def test_sqlalchemy_api_idempotency_survives_repository_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'api-idempotency.db'}"
    first = SqlAlchemyRepository(database_url, schema_translate_map=SCHEMA_MAP)
    first.create_schema_for_tests()
    record = ApiIdempotencyRecord(
        request_hash="a" * 64,
        operation="source.create",
        resource_id="src-1",
        response={"data": {"id": "src-1"}, "meta": {"request_id": "req-1"}},
    )
    first.save_api_idempotent("api:usr:source.create:key", record)

    restarted = SqlAlchemyRepository(database_url, schema_translate_map=SCHEMA_MAP)
    loaded = restarted.get_api_idempotent("api:usr:source.create:key")

    assert loaded == record


def test_event_api_cursor_pages_have_no_duplicates_or_omissions() -> None:
    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        for index in range(5):
            repository.save_event(_event(index))
        headers = _headers(client, "researcher")

        first = client.get("/api/v1/events?limit=2", headers=headers)
        second = client.get(
            "/api/v1/events",
            params={"limit": 2, "cursor": first.json()["meta"]["next_cursor"]},
            headers=headers,
        )
        third = client.get(
            "/api/v1/events",
            params={"limit": 2, "cursor": second.json()["meta"]["next_cursor"]},
            headers=headers,
        )

        ids = [
            item["id"]
            for response in (first, second, third)
            for item in response.json()["data"]
        ]
        assert ids == ["evt-04", "evt-03", "evt-02", "evt-01", "evt-00"]
        assert len(ids) == len(set(ids))
        assert third.json()["meta"]["next_cursor"] is None


@pytest.mark.parametrize("adapter", ["memory", "sqlalchemy"])
def test_repository_event_and_report_cursor_behavior_is_consistent(adapter: str, tmp_path) -> None:
    repository = _repository(adapter, tmp_path)
    with repository.transaction() as transaction:
        for index in range(4):
            transaction.save_event(_event(index))
            transaction.save_fact_card(_card(index))

    first_events = repository.list_events(limit=2)
    event_cursor = encode_cursor(first_events[-1].occurred_at, first_events[-1].id)
    second_events = repository.list_events(limit=2, cursor=event_cursor)
    first_reports = repository.list_fact_cards(limit=2)
    report_cursor = encode_cursor(first_reports[-1].as_of, first_reports[-1].id)
    second_reports = repository.list_fact_cards(limit=2, cursor=report_cursor)

    assert [value.id for value in first_events + second_events] == [
        "evt-03",
        "evt-02",
        "evt-01",
        "evt-00",
    ]
    assert [value.id for value in first_reports + second_reports] == [
        "rpt-03",
        "rpt-02",
        "rpt-01",
        "rpt-00",
    ]


def test_list_rejects_unknown_filters_invalid_cursor_and_oversized_limit() -> None:
    with TestClient(create_app()) as client:
        headers = _headers(client, "admin")
        assert client.get("/api/v1/events?unexpected=x", headers=headers).status_code == 422
        assert client.get("/api/v1/events?cursor=bad", headers=headers).status_code == 400
        assert client.get("/api/v1/events?limit=201", headers=headers).status_code == 422
        assert client.get("/api/v1/sources?unexpected=x", headers=headers).status_code == 422
        assert client.get("/api/v1/sources?cursor=bad", headers=headers).status_code == 400
        assert client.get("/api/v1/sources?limit=101", headers=headers).status_code == 422


def test_evidence_api_hides_document_content_for_publisher() -> None:
    from app.domain import Document, EvidenceSpan

    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        now = NOW
        repository.save_document(
            Document(
                id="doc-auth-1",
                source_id="src-1",
                source_tier="S",
                external_id="ext-auth-1",
                canonical_url="https://example.test/a",
                title="受限文档",
                content="机密正文不应对 publisher 返回",
                content_hash="h-auth",
                published_at=now,
                ingested_at=now,
            )
        )
        repository.save_evidence(
            EvidenceSpan(
                id="evd-auth-1",
                document_id="doc-auth-1",
                revision_id="rev-auth-1",
                locator={"block_id": "p1", "char_start": 0, "char_end": 4},
                excerpt="机密正文",
                excerpt_hash="eh",
                locator_type="html",
                extraction_method="html",
                extraction_version="html-blocks-v1",
                created_at=now,
            )
        )
        publisher = _headers(client, "publisher")
        researcher = _headers(client, "researcher")

        pub = client.get("/api/v1/evidence/evd-auth-1", headers=publisher)
        res = client.get("/api/v1/evidence/evd-auth-1", headers=researcher)

        assert pub.status_code == 200
        assert pub.json()["data"]["display_scope"] == "entry"
        assert pub.json()["data"]["document_content"] is None
        assert res.status_code == 200
        assert res.json()["data"]["display_scope"] == "full"
        assert "机密正文" in res.json()["data"]["document_content"]


def test_source_api_cursor_pages_have_no_duplicates_or_omissions() -> None:
    with TestClient(create_app()) as client:
        headers = _headers(client, "admin")
        created_ids: list[str] = []
        for index in range(5):
            response = client.post(
                "/api/v1/sources",
                headers=headers,
                json=_source_payload(code=f"src-page-{index:02d}"),
            )
            assert response.status_code == 201
            created_ids.append(response.json()["data"]["id"])

        first = client.get("/api/v1/sources?limit=2", headers=headers)
        second = client.get(
            "/api/v1/sources",
            params={"limit": 2, "cursor": first.json()["meta"]["next_cursor"]},
            headers=headers,
        )
        third = client.get(
            "/api/v1/sources",
            params={"limit": 2, "cursor": second.json()["meta"]["next_cursor"]},
            headers=headers,
        )

        ids = [
            item["id"]
            for response in (first, second, third)
            for item in response.json()["data"]
        ]
        assert len(ids) == len(set(ids))
        assert set(created_ids).issubset(set(ids))
        assert third.json()["meta"]["next_cursor"] is None


def test_ingest_run_api_cursor_pages_and_filter_whitelist() -> None:
    from app.domain import IngestRun, Source

    with TestClient(create_app()) as client:
        headers = _headers(client, "admin")
        repository = client.app.state.repository
        source = Source(
            id="src-runs",
            code="runs-source",
            name="Runs source",
            trust_tier="S",
            feed_url="https://source.example.test/feed.xml",
            allowed_domains=["source.example.test"],
        )
        repository.save_source(source)
        for index in range(5):
            repository.save_ingest_run(
                IngestRun(
                    id=f"run-{index:02d}",
                    source_id=source.id,
                    trigger="manual",
                    started_at=NOW + timedelta(minutes=index),
                    status="success",
                    finished_at=NOW + timedelta(minutes=index, seconds=10),
                    fetched=1,
                    processed=1,
                    quarantined=0,
                )
            )

        assert (
            client.get(
                f"/api/v1/sources/{source.id}/runs?unexpected=1",
                headers=headers,
            ).status_code
            == 422
        )

        first = client.get(
            f"/api/v1/sources/{source.id}/runs",
            params={"limit": 2},
            headers=headers,
        )
        second = client.get(
            f"/api/v1/sources/{source.id}/runs",
            params={"limit": 2, "cursor": first.json()["meta"]["next_cursor"]},
            headers=headers,
        )
        third = client.get(
            f"/api/v1/sources/{source.id}/runs",
            params={"limit": 2, "cursor": second.json()["meta"]["next_cursor"]},
            headers=headers,
        )

        ids = [
            item["id"]
            for response in (first, second, third)
            for item in response.json()["data"]
        ]
        assert ids == ["run-04", "run-03", "run-02", "run-01", "run-00"]
        assert third.json()["meta"]["next_cursor"] is None
