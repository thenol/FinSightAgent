from fastapi.testclient import TestClient

from app.main import create_app


def _auth_headers(client: TestClient, **extra: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}", **extra}


def test_ingest_and_query_event() -> None:
    with TestClient(create_app()) as client:
        headers = _auth_headers(client, **{"Idempotency-Key": "api-test-1"})
        response = client.post(
            "/api/v1/documents/ingest",
            headers=headers,
            json={
                "source_id": "sse",
                "source_tier": "S",
                "external_id": "sse-001",
                "url": "https://example.test/sse-001",
                "title": "示例公司（600000.SH）重大合同公告",
                "content": "公司与客户签署重大合同，合同金额为人民币1亿元。",
                "published_at": "2026-07-12T09:30:00+08:00",
            },
        )
        assert response.status_code == 201
        result = response.json()["data"]
        assert result["claim_status"] == "verified"

        event = client.get(f"/api/v1/events/{result['event_id']}", headers=headers)
        assert event.status_code == 200
        body = event.json()["data"]
        assert body["event_type"] == "major_contract"
        assert body["entity_ids"] == ["600000.SH"]
        assert body["fact_card_id"] == result["fact_card_id"]

        report = client.get(f"/api/v1/reports/{result['fact_card_id']}", headers=headers)
        assert report.status_code == 200
        assert report.json()["data"]["report_type"] == "fact_card"


def test_unknown_event_returns_404() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/events/evt_missing", headers=_auth_headers(client))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "EVENT_NOT_FOUND"
        assert response.json()["meta"]["request_id"] == response.headers["X-Request-ID"]


def test_validation_error_uses_standard_envelope_and_request_id() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/documents/ingest",
            headers=_auth_headers(client, **{"X-Request-ID": "request-from-client"}),
            json={"source_id": "sse"},
        )

        assert response.status_code == 422
        assert response.headers["X-Request-ID"] == "request-from-client"
        assert response.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
        assert response.json()["error"]["details"]
