from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


def test_admin_serves_spa_shell_when_built() -> None:
    if not (DIST / "index.html").is_file():
        return
    with TestClient(create_app()) as client:
        response = client.get("/admin")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "/admin/assets/" in response.text or "root" in response.text


def test_admin_spa_client_route_falls_back_to_index() -> None:
    if not (DIST / "index.html").is_file():
        return
    with TestClient(create_app()) as client:
        response = client.get("/admin/reviews")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
