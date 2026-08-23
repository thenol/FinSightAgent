"""Login failure rate limiting."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.auth import PASSWORD_HASH
from app.domain import User
from app.main import create_app
from app.platform.ids import new_id


def test_login_locks_after_repeated_failures(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    monkeypatch.setenv("FINSIGHT_LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("FINSIGHT_LOGIN_LOCKOUT_SECONDS", "60")
    monkeypatch.setenv("FINSIGHT_LOGIN_FAILURE_WINDOW_SECONDS", "300")
    application = create_app()
    with TestClient(application, client=("testclient", 50000)) as client:
        client.app.state.repository.save_user(
            User(
                id=new_id("usr"),
                username="locked-user",
                password_hash=PASSWORD_HASH.hash("correct"),
                role="admin",
            )
        )
        for _ in range(3):
            failed = client.post(
                "/api/v1/auth/login",
                json={"username": "locked-user", "password": "wrong"},
            )
            assert failed.status_code == 401
        locked = client.post(
            "/api/v1/auth/login",
            json={"username": "locked-user", "password": "wrong"},
        )
        assert locked.status_code == 429
        assert locked.json()["error"]["code"] == "AUTH_LOGIN_LOCKED"
        assert locked.headers.get("Retry-After")
        success = client.post(
            "/api/v1/auth/login",
            json={"username": "locked-user", "password": "correct"},
        )
        assert success.status_code == 429


def test_successful_login_clears_failure_counter(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    monkeypatch.setenv("FINSIGHT_LOGIN_MAX_FAILURES", "3")
    application = create_app()
    with TestClient(application, client=("testclient", 50001)) as client:
        client.app.state.repository.save_user(
            User(
                id=new_id("usr"),
                username="recover-user",
                password_hash=PASSWORD_HASH.hash("correct"),
                role="admin",
            )
        )
        client.post(
            "/api/v1/auth/login",
            json={"username": "recover-user", "password": "wrong"},
        )
        client.post(
            "/api/v1/auth/login",
            json={"username": "recover-user", "password": "wrong"},
        )
        success = client.post(
            "/api/v1/auth/login",
            json={"username": "recover-user", "password": "correct"},
        )
        assert success.status_code == 200
        again = client.post(
            "/api/v1/auth/login",
            json={"username": "recover-user", "password": "correct"},
        )
        assert again.status_code == 200
