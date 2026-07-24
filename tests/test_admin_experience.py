import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
LEGACY_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "admin" / "admin.js"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_admin_serves_html_shell(client: TestClient) -> None:
    response = client.get("/admin")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    text = response.text
    if (DIST / "index.html").is_file():
        assert 'id="root"' in text or "/admin/assets/" in text
    else:
        assert "管理后台尚未构建" in text or "FinSight" in text


def test_admin_assets_are_same_origin_and_cacheable(client: TestClient) -> None:
    if not LEGACY_JS.is_file():
        pytest.skip("legacy admin assets not present")
    for asset_url, media_type in (
        ("/admin/assets/admin.css", "text/css"),
        ("/admin/assets/admin.js", "javascript"),
    ):
        parsed_url = urlsplit(asset_url)
        assert not parsed_url.scheme and not parsed_url.netloc
        response = client.get(asset_url)
        assert response.status_code == 200
        assert media_type in response.headers["content-type"].lower()
        cache_control = response.headers.get("cache-control", "").lower()
        max_age = re.search(r"(?:s-maxage|max-age)=(\d+)", cache_control)
        assert "no-store" not in cache_control
        assert max_age is not None and int(max_age.group(1)) > 0


def test_legacy_admin_javascript_uses_non_blocking_controls(client: TestClient) -> None:
    if not LEGACY_JS.is_file():
        pytest.skip("legacy admin assets not present")
    response = client.get("/admin/assets/admin.js")
    assert response.status_code == 200
    javascript = response.text
    assert re.search(r"\b(?:alert|prompt)\s*\(", javascript, re.IGNORECASE) is None
    assert re.search(r"\bonclick\s*=", javascript, re.IGNORECASE) is None
    assert "confirmDialog" in javascript
    assert "/api/v1/reviews" in javascript and "/decision" in javascript
    assert "/api/v1/evidence/" in javascript
    assert re.search(r"""["']/api/v1/reports(?:\?[^"']*)?["']""", javascript)


def test_admin_review_queue_still_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/reviews?status_filter=pending")
    assert response.status_code == 401
