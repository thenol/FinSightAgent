from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_system_status_and_guarded_operations(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    with TestClient(create_app()) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        )
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        status = client.get("/api/v1/admin/system/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["data"]["platform"]["repository"] == "memory"
        assert "outbox-worker" in {item["name"] for item in status.json()["data"]["workers"]}

        missing_target = client.post(
            "/api/v1/admin/system/actions/retry-outbox", headers=headers, json={}
        )
        assert missing_target.status_code == 422

        collection = client.get("/api/v1/admin/system/collection", headers=headers)
        assert collection.status_code == 200
        assert collection.json()["data"]["config"]["scheduler_enabled"] is True
        paused = client.patch(
            "/api/v1/admin/system/collection",
            headers=headers,
            json={"scheduler_enabled": False},
        )
        assert paused.status_code == 200
        assert paused.json()["data"]["scheduler_enabled"] is False
