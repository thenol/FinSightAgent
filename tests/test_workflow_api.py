"""API-level workflow create/run without a workflow worker."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.auth import PASSWORD_HASH
from app.domain import Event, User, WorkflowRun
from app.main import create_app


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_workflow_executes_graph_by_default() -> None:
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        repository.save_user(
            User(
                id="usr-wf-api",
                username="wf-api",
                password_hash=PASSWORD_HASH.hash("secret123"),
                role="researcher",
            )
        )
        repository.save_event(
            Event(
                id="evt-wf-api",
                event_type="earnings_guidance",
                status="triaged",
                title="API workflow",
                entity_ids=[],
                document_ids=[],
                importance=0.5,
                urgency="normal",
                occurred_at=datetime.now(timezone.utc),
            )
        )
        headers = _login(client, "wf-api", "secret123")
        created = client.post(
            "/api/v1/events/evt-wf-api/workflows",
            headers={**headers, "Idempotency-Key": "wf-create-1"},
            json={"trigger_id": "manual"},
        )
        assert created.status_code == 201
        body = created.json()["data"]
        assert body["status"] in {"succeeded", "waiting_review", "failed"}
        assert body["status"] != "pending"

        attempts = client.get(f"/api/v1/workflows/{body['id']}/attempts", headers=headers)
        assert attempts.status_code == 200
        assert len(attempts.json()["data"]) >= 1

        budget = client.get(f"/api/v1/workflows/{body['id']}/budget", headers=headers)
        assert budget.status_code == 200
        assert len(budget.json()["data"]) >= 1


def test_run_endpoint_starts_pending_workflow() -> None:
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        repository.save_user(
            User(
                id="usr-wf-run",
                username="wf-run",
                password_hash=PASSWORD_HASH.hash("secret123"),
                role="admin",
            )
        )
        repository.save_event(
            Event(
                id="evt-wf-run",
                event_type="earnings_guidance",
                status="triaged",
                title="Pending run",
                entity_ids=[],
                document_ids=[],
                importance=0.5,
                urgency="normal",
                occurred_at=datetime.now(timezone.utc),
            )
        )
        headers = _login(client, "wf-run", "secret123")
        created = client.post(
            "/api/v1/events/evt-wf-run/workflows",
            headers={**headers, "Idempotency-Key": "wf-pending-1"},
            json={"trigger_id": "manual", "execute": False},
        )
        assert created.status_code == 201
        workflow_id = created.json()["data"]["id"]
        assert created.json()["data"]["status"] == "pending"

        started = client.post(
            f"/api/v1/workflows/{workflow_id}/run",
            headers={**headers, "Idempotency-Key": "wf-run-1"},
            json={},
        )
        assert started.status_code == 200
        assert started.json()["data"]["status"] in {"succeeded", "waiting_review", "failed"}

        again = client.post(
            f"/api/v1/workflows/{workflow_id}/run",
            headers={**headers, "Idempotency-Key": "wf-run-2"},
            json={},
        )
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "WORKFLOW_NOT_RUNNABLE"


def test_run_endpoint_rejects_missing_workflow() -> None:
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        repository.save_user(
            User(
                id="usr-wf-miss",
                username="wf-miss",
                password_hash=PASSWORD_HASH.hash("secret123"),
                role="admin",
            )
        )
        headers = _login(client, "wf-miss", "secret123")
        response = client.post("/api/v1/workflows/wfr_missing/run", headers=headers, json={})
        assert response.status_code == 404


def test_create_with_execute_false_leaves_pending() -> None:
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        repository.save_user(
            User(
                id="usr-wf-async",
                username="wf-async",
                password_hash=PASSWORD_HASH.hash("secret123"),
                role="researcher",
            )
        )
        now = datetime.now(timezone.utc)
        repository.save_event(
            Event(
                id="evt-wf-async",
                event_type="earnings_guidance",
                status="triaged",
                title="Async",
                entity_ids=[],
                document_ids=[],
                importance=0.5,
                urgency="normal",
                occurred_at=now,
            )
        )
        headers = _login(client, "wf-async", "secret123")
        created = client.post(
            "/api/v1/events/evt-wf-async/workflows",
            headers=headers,
            json={"execute": False},
        )
        assert created.status_code == 201
        assert created.json()["data"]["status"] == "pending"
        # leftover pending is fine; ensure repository shape
        stored = repository.get_workflow_run(created.json()["data"]["id"])
        assert isinstance(stored, WorkflowRun)
        assert stored.status == "pending"
