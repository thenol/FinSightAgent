from fastapi.testclient import TestClient

from app.main import create_app


def test_development_creates_default_admin() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
    assert response.status_code == 200


def test_bootstrap_admin_creates_local_account(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_BOOTSTRAP_ADMIN_USERNAME", "ops")
    monkeypatch.setenv("FINSIGHT_BOOTSTRAP_ADMIN_PASSWORD", "ops-admin-123")
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "ops", "password": "ops-admin-123"},
        )
    assert response.status_code == 200
