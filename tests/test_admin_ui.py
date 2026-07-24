from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.auth import PASSWORD_HASH
from app.domain import (
    Document,
    EvidenceSpan,
    FactCard,
    ReviewTask,
    User,
    WorkflowRun,
)
from app.main import create_app
from app.publishing.service import FactCardService


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def test_admin_console_exposes_ops_hooks() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/admin")
        script = client.get("/admin/assets/admin.js")
    assert response.status_code == 200
    assert script.status_code == 200
    text = script.text
    assert "/api/v1/sources/seed" in text
    assert "/api/v1/reports/" in text and "/diff/" in text
    assert "/api/v1/evidence/" in text
    assert "/api/v1/workflows/" in text and "/resume" in text
    assert "sync-source" in text
    assert "downgrade_fact_only" in text


def test_admin_api_surface_for_console() -> None:
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        admin = User(
            id="usr-admin-console",
            username="admin-console",
            password_hash=PASSWORD_HASH.hash("secret123"),
            role="admin",
        )
        repository.save_user(admin)
        token = _login(client, "admin-console", "secret123")
        headers = {"Authorization": f"Bearer {token}"}

        seed = client.post("/api/v1/sources/seed", headers=headers)
        assert seed.status_code == 200
        assert seed.json()["data"]["inserted"] >= 1
        again = client.post("/api/v1/sources/seed", headers=headers)
        assert again.json()["data"]["inserted"] == 0

        sources = client.get("/api/v1/sources", headers=headers).json()["data"]
        source_id = sources[0]["id"]
        patched = client.patch(
            f"/api/v1/sources/{source_id}",
            headers=headers,
            json={"status": "disabled"},
        )
        assert patched.status_code == 200
        assert patched.json()["data"]["status"] == "disabled"

        now = datetime.now(timezone.utc)
        repository.save_document(
            Document(
                id="doc-ev-1",
                source_id=source_id,
                source_tier="A",
                external_id="ext-1",
                canonical_url="https://example.com/a",
                title="证据文档",
                content="前文 ALPHA 金额上升 后文",
                content_hash="h1",
                published_at=now,
                ingested_at=now,
            )
        )
        repository.save_evidence(
            EvidenceSpan(
                id="evd-1",
                document_id="doc-ev-1",
                revision_id="rev-1",
                locator={"block_id": "p1", "char_start": 3, "char_end": 12},
                excerpt="ALPHA 金额",
                excerpt_hash="eh",
                locator_type="html_paragraph",
                extraction_method="html",
                extraction_version="html-blocks-v1",
                created_at=now,
            )
        )
        evidence = client.get("/api/v1/evidence/evd-1", headers=headers)
        assert evidence.status_code == 200
        body = evidence.json()["data"]
        assert body["excerpt"] == "ALPHA 金额"
        assert body["document_title"] == "证据文档"
        assert "ALPHA" in body["document_content"]

        repository.save_fact_card(
            FactCard(
                id="rpt-admin-1",
                event_id="evt-admin-1",
                version=1,
                status="review_required",
                title="v1",
                summary="old",
                claim_ids=["clm-1"],
                as_of=now,
            )
        )
        updated = FactCardService(repository).transition(
            repository.get_fact_card("rpt-admin-1"), "approved", "ok"
        )
        diff = client.get(
            f"/api/v1/reports/rpt-admin-1/diff/{updated.id}",
            headers=headers,
        )
        assert diff.status_code == 200
        assert "status" in diff.json()["data"]["changes"]

        repository.save_workflow_run(
            WorkflowRun(
                id="wf-admin-1",
                event_id="evt-admin-1",
                trigger_id="manual",
                status="waiting_review",
                as_of=now,
                current_node="company",
                blackboard={"degradation_reason": "BUDGET_HARD_LIMIT"},
            )
        )
        listed = client.get("/api/v1/workflows?status_filter=waiting_review", headers=headers)
        assert listed.status_code == 200
        assert any(w["id"] == "wf-admin-1" for w in listed.json()["data"])

        budget = client.get("/api/v1/workflows/wf-admin-1/budget", headers=headers)
        attempts = client.get("/api/v1/workflows/wf-admin-1/attempts", headers=headers)
        assert budget.status_code == 200
        assert attempts.status_code == 200

        repository.save_review_task(
            ReviewTask(
                id="rvw-admin-1",
                object_type="workflow",
                object_id="wf-admin-1",
                reason_code="BUDGET_HARD_LIMIT",
                allowed_decisions=["approve", "downgrade_to_fact_card", "reject"],
                resume_from="company",
            )
        )
        detail = client.get("/api/v1/reviews/rvw-admin-1", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["data"]["object_type"] == "workflow"

        resume = client.post(
            "/api/v1/workflows/wf-admin-1/resume",
            headers=headers,
            json={
                "trigger": "downgrade_fact_only",
                "force_fact_only": True,
                "reason": "admin-test",
            },
        )
        assert resume.status_code == 200
        assert resume.json()["data"]["status"] in {"succeeded", "waiting_review", "failed"}
