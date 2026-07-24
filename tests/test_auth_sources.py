from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.auth import PASSWORD_HASH
from app.domain import FactCard, ReviewTask, User
from app.main import create_app
from app.platform.ids import new_id
from app.publishing.service import FactCardService


def test_admin_can_login_and_create_source(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    application = create_app()
    with TestClient(application) as client:
        client.app.state.repository.save_user(
            User(
                id=new_id("usr"),
                username="operator",
                password_hash=PASSWORD_HASH.hash("secret"),
                role="admin",
            )
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "operator", "password": "secret"},
        )
        assert login.status_code == 200
        token = login.json()["data"]["access_token"]

        response = client.post(
            "/api/v1/sources",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "code": "official-rss",
                "name": "Official RSS",
                "trust_tier": "S",
                "feed_url": "https://source.example.com/feed.xml",
                "allowed_domains": ["source.example.com"],
                "crawl_interval_seconds": 900,
            },
        )
        assert response.status_code == 201
        assert response.json()["data"]["crawl_interval_seconds"] == 900
        source_id = response.json()["data"]["id"]

        # Disabled sources are skipped by sync-all.
        client.patch(
            f"/api/v1/sources/{source_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "disabled"},
        )
        sync_all = client.post(
            "/api/v1/sources/sync-all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert sync_all.status_code == 200
        assert sync_all.json()["data"]["synced"] == 0

        client.patch(
            f"/api/v1/sources/{source_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        runs = client.get(
            f"/api/v1/sources/{source_id}/runs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert runs.status_code == 200
        assert isinstance(runs.json()["data"], list)
        assert response.json()["data"]["code"] == "official-rss"

        audit = client.get(
            "/api/v1/audit-logs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert audit.status_code == 200
        assert {item["action"] for item in audit.json()["data"]} >= {
            "auth.login",
            "source.create",
        }


def test_researcher_cannot_create_source() -> None:
    application = create_app()
    with TestClient(application) as client:
        client.app.state.repository.save_user(
            User(
                id=new_id("usr"),
                username="researcher",
                password_hash=PASSWORD_HASH.hash("secret"),
                role="researcher",
            )
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "researcher", "password": "secret"},
        )
        token = login.json()["data"]["access_token"]
        response = client.post(
            "/api/v1/sources",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "code": "restricted",
                "name": "Restricted",
                "trust_tier": "S",
                "feed_url": "https://source.example.com/feed.xml",
                "allowed_domains": ["source.example.com"],
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_review_and_publish_report_require_separate_roles() -> None:
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        reviewer = User(
            id=new_id("usr"),
            username="reviewer",
            password_hash=PASSWORD_HASH.hash("secret"),
            role="reviewer",
        )
        publisher = User(
            id=new_id("usr"),
            username="publisher",
            password_hash=PASSWORD_HASH.hash("secret"),
            role="publisher",
        )
        repository.save_user(reviewer)
        repository.save_user(publisher)
        repository.save_fact_card(
            FactCard(
                id="rpt-test",
                event_id="evt-test",
                version=1,
                status="review_required",
                title="Test report",
                summary="Needs review",
                claim_ids=[],
                as_of=datetime.now(timezone.utc),
            )
        )

        reviewer_token = client.post(
            "/api/v1/auth/login", json={"username": "reviewer", "password": "secret"}
        ).json()["data"]["access_token"]
        publisher_token = client.post(
            "/api/v1/auth/login", json={"username": "publisher", "password": "secret"}
        ).json()["data"]["access_token"]
        review = client.post(
            "/api/v1/reports/rpt-test/transition",
            headers={"Authorization": f"Bearer {reviewer_token}"},
            json={"status": "approved"},
        )
        assert review.status_code == 200
        publish = client.post(
            "/api/v1/reports/rpt-test/transition",
            headers={"Authorization": f"Bearer {publisher_token}"},
            json={"status": "published"},
        )
        assert publish.status_code == 200
        versions = client.get(
            "/api/v1/events/evt-test/reports",
            headers={"Authorization": f"Bearer {publisher_token}"},
        )
        assert versions.status_code == 200
        assert versions.json()["data"][0]["version"] == 3
        assert len(versions.json()["data"]) == 3
        assert versions.json()["data"][0]["supersedes_report_id"] != "rpt-test"
        original = client.get(
            "/api/v1/reports/rpt-test",
            headers={"Authorization": f"Bearer {publisher_token}"},
        )
        assert original.json()["data"]["status"] == "review_required"


def test_report_version_diff_preserves_original() -> None:
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        original = FactCard(
            id="rpt-original",
            event_id="evt-diff",
            version=1,
            status="review_required",
            title="Original",
            summary="Needs review",
            claim_ids=["clm-1"],
            as_of=datetime.now(timezone.utc),
        )
        repository.save_fact_card(original)
        replacement = FactCardService(repository).transition(original, "approved", "review passed")
        token = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        ).json()["data"]["access_token"]

        response = client.get(
            f"/api/v1/reports/{original.id}/diff/{replacement.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["data"]["changes"]["status"] == {
            "from": "review_required",
            "to": "approved",
        }
        assert repository.get_fact_card(original.id) == original


def test_reviewer_decides_pending_report_task_and_audits() -> None:
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        reviewer = User(
            id="usr-review",
            username="reviewer-queue",
            password_hash=PASSWORD_HASH.hash("secret"),
            role="reviewer",
        )
        repository.save_user(reviewer)
        repository.save_fact_card(
            FactCard(
                id="rpt-queue",
                event_id="evt-queue",
                version=1,
                status="review_required",
                title="Queue",
                summary="Queue",
                claim_ids=[],
                as_of=datetime.now(timezone.utc),
            )
        )
        repository.save_review_task(
            ReviewTask(
                id="rvt-queue",
                object_type="report",
                object_id="rpt-queue",
                reason_code="REPORT_REVIEW_REQUIRED",
                allowed_decisions=["approve", "return", "reject"],
                created_at=datetime.now(timezone.utc),
            )
        )
        token = client.post(
            "/api/v1/auth/login", json={"username": "reviewer-queue", "password": "secret"}
        ).json()["data"]["access_token"]
        result = client.post(
            "/api/v1/reviews/rvt-queue/decision",
            headers={"Authorization": f"Bearer {token}"},
            json={"decision": "approve", "comment": "evidence checked"},
        )
        assert result.status_code == 200
        assert result.json()["data"]["status"] == "approved"
        assert repository.get_review_task("rvt-queue").status == "decided"


def test_publisher_cannot_return_report_for_revision() -> None:
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        publisher = User(
            id=new_id("usr"),
            username="publisher",
            password_hash=PASSWORD_HASH.hash("secret"),
            role="publisher",
        )
        repository.save_user(publisher)
        repository.save_fact_card(
            FactCard(
                id="rpt-published",
                event_id="evt-published",
                version=1,
                status="approved",
                title="Published",
                summary="Published",
                claim_ids=[],
                as_of=datetime.now(timezone.utc),
            )
        )
        token = client.post(
            "/api/v1/auth/login", json={"username": "publisher", "password": "secret"}
        ).json()["data"]["access_token"]
        response = client.post(
            "/api/v1/reports/rpt-published/transition",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "needs_revision"},
        )
        assert response.status_code == 403
