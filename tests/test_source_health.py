"""来源健康检查 API 测试。"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.auth import PASSWORD_HASH
from app.domain import IngestRun, Source, User
from app.main import create_app
from app.platform.ids import new_id


def test_source_health_reflects_status_and_runs(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_ENV", "test")
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        repository.save_user(
            User(
                id=new_id("usr"),
                username="health-admin",
                password_hash=PASSWORD_HASH.hash("secret"),
                role="admin",
            )
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "health-admin", "password": "secret"},
        )
        assert login.status_code == 200
        token = login.json()["data"]["access_token"]

        source = Source(
            id=new_id("src"),
            code="health-src",
            name="Health Test",
            trust_tier="A",
            feed_url="https://example.test/feed",
            allowed_domains=("example.test",),
            adapter_type="rss",
            status="active",
            consecutive_failures=0,
            last_success_at=datetime.now(timezone.utc),
        )
        repository.save_source(source)

        run = IngestRun(
            id=new_id("ing"),
            source_id=source.id,
            trigger="manual",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            status="succeeded",
            fetched=10,
            processed=8,
            quarantined=0,
        )
        repository.save_ingest_run(run)

        response = client.get(
            f"/api/v1/sources/{source.id}/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["health"] == "healthy"
        assert data["consecutive_failures"] == 0
        assert data["source"]["id"] == source.id
        assert len(data["recent_runs"]) == 1
        assert data["last_run"]["id"] == run.id


def test_source_health_degraded_with_failures(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_ENV", "test")
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        repository.save_user(
            User(
                id=new_id("usr"),
                username="health-admin",
                password_hash=PASSWORD_HASH.hash("secret"),
                role="admin",
            )
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "health-admin", "password": "secret"},
        )
        token = login.json()["data"]["access_token"]

        source = Source(
            id=new_id("src"),
            code="degraded-src",
            name="Degraded Test",
            trust_tier="A",
            feed_url="https://example.test/feed",
            allowed_domains=("example.test",),
            adapter_type="rss",
            status="active",
            consecutive_failures=3,
            last_success_at=None,
        )
        repository.save_source(source)

        response = client.get(
            f"/api/v1/sources/{source.id}/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["health"] == "degraded"
        assert data["consecutive_failures"] == 3


def test_source_health_disabled_status(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_ENV", "test")
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        repository.save_user(
            User(
                id=new_id("usr"),
                username="health-admin",
                password_hash=PASSWORD_HASH.hash("secret"),
                role="admin",
            )
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "health-admin", "password": "secret"},
        )
        token = login.json()["data"]["access_token"]

        source = Source(
            id=new_id("src"),
            code="disabled-src",
            name="Disabled Test",
            trust_tier="A",
            feed_url="https://example.test/feed",
            allowed_domains=("example.test",),
            adapter_type="rss",
            status="disabled",
            consecutive_failures=0,
        )
        repository.save_source(source)

        response = client.get(
            f"/api/v1/sources/{source.id}/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["health"] == "disabled"


def test_source_health_not_found(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_ENV", "test")
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        repository.save_user(
            User(
                id=new_id("usr"),
                username="health-admin",
                password_hash=PASSWORD_HASH.hash("secret"),
                role="admin",
            )
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "health-admin", "password": "secret"},
        )
        token = login.json()["data"]["access_token"]

        response = client.get(
            "/api/v1/sources/src_missing/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
