from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.domain import MergeReviewTask, ReviewTask
from app.main import create_app
from app.platform.ids import new_id


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


def test_merge_review_lifecycle() -> None:
    with TestClient(create_app()) as client:
        headers = _auth_headers(client)
        repository = client.app.state.repository
        document_id = new_id("doc")
        task = MergeReviewTask(
            id=new_id("mrg"),
            document_id=document_id,
            candidates=["evt_1", "evt_2"],
            status="open",
            created_at=datetime.now(timezone.utc),
        )
        repository.save_merge_review_task(task)

        response = client.get("/api/v1/merge-reviews", headers=headers)
        assert response.status_code == 200
        body = response.json()["data"]
        assert len(body) == 1
        assert body[0]["document_id"] == document_id
        assert body[0]["status"] == "open"

        response = client.get(f"/api/v1/merge-reviews/{task.id}", headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["id"] == task.id


def test_merge_review_decision() -> None:
    with TestClient(create_app()) as client:
        headers = _auth_headers(client)
        repository = client.app.state.repository
        task = MergeReviewTask(
            id=new_id("mrg"),
            document_id=new_id("doc"),
            candidates=["evt_1"],
            status="open",
            created_at=datetime.now(timezone.utc),
        )
        repository.save_merge_review_task(task)

        response = client.post(
            f"/api/v1/merge-reviews/{task.id}/decision",
            headers=headers,
            json={"decision": "merge", "comment": "looks related"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "decided"
        assert data["decision"] == "merge"

        response = client.post(
            f"/api/v1/merge-reviews/{task.id}/decision",
            headers=headers,
            json={"decision": "skip", "comment": "retry"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "MERGE_REVIEW_ALREADY_DECIDED"


def test_merge_review_invalid_decision() -> None:
    with TestClient(create_app()) as client:
        headers = _auth_headers(client)
        repository = client.app.state.repository
        task = MergeReviewTask(
            id=new_id("mrg"),
            document_id=new_id("doc"),
            candidates=["evt_1"],
            status="open",
            created_at=datetime.now(timezone.utc),
        )
        repository.save_merge_review_task(task)

        response = client.post(
            f"/api/v1/merge-reviews/{task.id}/decision",
            headers=headers,
            json={"decision": "approve", "comment": "wrong decision"},
        )
        assert response.status_code == 422


def test_review_approve_report() -> None:
    with TestClient(create_app()) as client:
        headers = _auth_headers(client, **{"Idempotency-Key": "api-test-approve"})
        ingest = client.post(
            "/api/v1/documents/ingest",
            headers=headers,
            json={
                "source_id": "sse",
                "source_tier": "S",
                "external_id": "sse-approve-001",
                "url": "https://example.test/sse-approve-001",
                "title": "示例公司（600001.SH）重大合同公告",
                "content": "公司与客户签署重大合同，合同金额为人民币2亿元。",
                "published_at": "2026-07-12T09:30:00+08:00",
            },
        )
        assert ingest.status_code == 201
        fact_card_id = ingest.json()["data"]["fact_card_id"]

        repository = client.app.state.repository
        task = ReviewTask(
            id=new_id("rvt"),
            object_type="report",
            object_id=fact_card_id,
            reason_code="manual_review",
            allowed_decisions=["approve", "return", "reject"],
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        repository.save_review_task(task)

        response = client.post(
            f"/api/v1/reviews/{task.id}/decision",
            headers=headers,
            json={"decision": "approve", "comment": "approved via ui"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "approved"
