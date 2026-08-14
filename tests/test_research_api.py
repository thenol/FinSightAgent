from fastapi.testclient import TestClient

from app.main import create_app


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_research_plan():
    with TestClient(create_app()) as client:
        headers = _auth_headers(client)
        payload = {
            "question": "某公司发布业绩预告的影响",
            "as_of": "2026-08-14T00:00:00Z",
            "execute": False,
        }
        response = client.post("/api/v1/research", json=payload, headers=headers)
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["question"] == payload["question"]
        assert data["status"] == "ready"
        assert len(data["tasks"]) > 0


def test_create_and_execute_research():
    with TestClient(create_app()) as client:
        headers = _auth_headers(client)
        payload = {
            "question": "某公司净利润增长的影响",
            "as_of": "2026-08-14T00:00:00Z",
            "execute": True,
        }
        response = client.post("/api/v1/research", json=payload, headers=headers)
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["status"] == "succeeded"


def test_execute_research_endpoint():
    with TestClient(create_app()) as client:
        headers = _auth_headers(client)
        create_response = client.post(
            "/api/v1/research",
            json={"question": "测试问题", "execute": False},
            headers=headers,
        )
        plan_id = create_response.json()["data"]["id"]
        exec_response = client.post(
            f"/api/v1/research/{plan_id}/execute",
            headers=headers,
        )
        assert exec_response.status_code == 200
        assert exec_response.json()["data"]["status"] == "succeeded"


def test_get_research_plan():
    with TestClient(create_app()) as client:
        headers = _auth_headers(client)
        create_response = client.post(
            "/api/v1/research",
            json={"question": "测试问题", "execute": False},
            headers=headers,
        )
        plan_id = create_response.json()["data"]["id"]
        get_response = client.get(f"/api/v1/research/{plan_id}", headers=headers)
        assert get_response.status_code == 200
        assert get_response.json()["data"]["id"] == plan_id


def test_list_research_tasks():
    with TestClient(create_app()) as client:
        headers = _auth_headers(client)
        create_response = client.post(
            "/api/v1/research",
            json={"question": "测试问题", "execute": False},
            headers=headers,
        )
        plan_id = create_response.json()["data"]["id"]
        tasks_response = client.get(f"/api/v1/research/{plan_id}/tasks", headers=headers)
        assert tasks_response.status_code == 200
        assert len(tasks_response.json()["data"]) > 0


def test_create_research_with_missing_event():
    with TestClient(create_app()) as client:
        headers = _auth_headers(client)
        payload = {"question": "测试", "event_id": "evt_missing", "execute": False}
        response = client.post("/api/v1/research", json=payload, headers=headers)
        assert response.status_code == 404


def test_research_requires_auth():
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/research", json={"question": "测试"})
        assert response.status_code == 401
