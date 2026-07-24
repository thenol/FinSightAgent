import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.model_gateway.config import create_provider, resolve_provider_for_operation, upsert_binding
from app.model_gateway.providers import OpenAICompatibleProvider
from app.model_gateway.secrets import SecretBox, api_key_status_label, redact_sensitive_mapping
from app.model_gateway.service import ModelGateway, ModelRequest
from app.platform.repository import InMemoryRepository


def test_secret_box_roundtrip_and_hint() -> None:
    box = SecretBox.from_settings()
    token = box.encrypt("sk-test-key-123456")
    assert token != "sk-test-key-123456"
    assert box.decrypt(token) == "sk-test-key-123456"
    assert api_key_status_label(True) == "configured"
    assert api_key_status_label(False) == ""


def test_redact_sensitive_mapping_strips_keys() -> None:
    payload = {
        "code": "openai",
        "api_key": "sk-secret-should-not-leak",
        "nested": {"authorization": "Bearer abc", "note": "sk-abcdefghijklmnopqrstuvwxyz"},
    }
    redacted = redact_sensitive_mapping(payload)
    assert redacted["api_key"] == "<redacted>"
    assert "sk-secret" not in str(redacted)
    assert redacted["nested"]["authorization"] == "<redacted>"
    assert "<redacted-key>" in redacted["nested"]["note"]


def test_openai_compatible_provider_parses_chat_completion(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true, "score": 1}'}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            assert url.endswith("/chat/completions")
            assert headers["Authorization"].startswith("Bearer ")
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    provider = OpenAICompatibleProvider(
        name="openai",
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="sk-test",
    )
    output = provider.invoke(
        ModelRequest(
            operation="fact_check",
            input_schema_version="v1",
            output_schema_version="v1",
            payload={"x": 1},
        )
    )
    assert output["ok"] is True
    assert output["operation"] == "fact_check"


def test_gateway_uses_bound_provider_hot_reload() -> None:
    repository = InMemoryRepository()
    secrets = SecretBox.from_settings()
    config = create_provider(
        repository,
        secrets,
        code="local_stub",
        display_name="Local",
        protocol="deterministic",
        base_url="",
        model="deterministic-v1",
        api_key="",
        is_default=True,
    )
    upsert_binding(repository, agent_key="fact_check", provider_id=config.id)
    gateway = ModelGateway(repository, secrets=secrets)
    response = gateway.invoke(
        ModelRequest(
            operation="fact_check",
            input_schema_version="v1",
            output_schema_version="v1",
            payload={"hello": "world"},
            max_cost_usd=1,
        )
    )
    assert response.payload["operation"] == "fact_check"
    run = repository.list_model_runs()[0]
    assert run.provider == "local_stub"


def test_resolve_falls_back_without_config() -> None:
    repository = InMemoryRepository()
    provider = resolve_provider_for_operation(repository, SecretBox.from_settings(), "synthesize")
    assert provider.name == "deterministic"


def test_resolve_falls_back_on_api_key_decrypt_failure() -> None:
    repository = InMemoryRepository()
    writer = SecretBox.from_settings()
    config = create_provider(
        repository,
        writer,
        code="broken_key",
        display_name="Broken",
        protocol="openai_compatible",
        base_url="https://example.test/v1",
        model="gpt-test",
        api_key="sk-should-not-decrypt-with-other-box",
        is_default=True,
    )
    upsert_binding(repository, agent_key="fact_check", provider_id=config.id)
    other = SecretBox(SecretBox.generate_key().encode("ascii"))
    provider = resolve_provider_for_operation(repository, other, "fact_check")
    assert provider.name == "deterministic-fallback"
    audits = [
        item
        for item in repository.list_audit_logs()
        if item.action == "llm.provider_fallback"
    ]
    assert len(audits) == 1
    assert audits[0].details["reason"] == "LLM_API_KEY_DECRYPT_FAILED"
    assert audits[0].object_id == config.id


def test_admin_llm_api_crud_and_binding(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    llm_path = tmp_path / "llm_config.json"
    with TestClient(create_app(llm_config_path=str(llm_path))) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert login.status_code == 200
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        presets = client.get("/api/v1/llm/presets", headers=headers)
        assert presets.status_code == 200
        assert any(item["code"] == "openai" for item in presets.json()["data"])

        created = client.post(
            "/api/v1/llm/providers",
            headers=headers,
            json={
                "code": "deepseek_main",
                "display_name": "DeepSeek Main",
                "protocol": "openai_compatible",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
                "api_key": "sk-secret-should-not-leak",
                "is_default": True,
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()["data"]
        assert body["api_key_configured"] is True
        assert body["api_key_hint"] == "configured"
        assert "sk-secret" not in str(body)
        provider_id = body["id"]

        listed = client.get("/api/v1/llm/providers", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["data"][0]["is_default"] is True
        assert listed.json()["data"][0]["api_key_hint"] == "configured"

        rotated = client.post(
            f"/api/v1/llm/providers/{provider_id}/rotate-key",
            headers=headers,
            json={"api_key": "sk-rotated-new-key-value"},
        )
        assert rotated.status_code == 200
        assert rotated.json()["data"]["api_key_configured"] is True
        assert "sk-rotated" not in str(rotated.json())

        audits = client.get("/api/v1/audit-logs?limit=50", headers=headers)
        assert audits.status_code == 200
        payload = audits.json()["data"]
        items = payload if isinstance(payload, list) else payload.get("items", [])
        actions = [item["action"] for item in items]
        assert "llm.provider.rotate_key" in actions
        assert "sk-rotated" not in str(audits.json())

        bound = client.put(
            "/api/v1/llm/bindings",
            headers=headers,
            json={"agent_key": "company_analysis", "provider_id": provider_id},
        )
        assert bound.status_code == 200
        assert bound.json()["data"]["provider_id"] == provider_id

        bulk = client.put(
            "/api/v1/llm/bindings/bulk",
            headers=headers,
            json={"provider_id": provider_id},
        )
        assert bulk.status_code == 200
        bulk_items = bulk.json()["data"]
        assert len(bulk_items) == 4
        assert {item["agent_key"] for item in bulk_items} == {
            "fact_check",
            "company_analysis",
            "skeptic_review",
            "synthesize",
        }
        assert all(item["provider_id"] == provider_id for item in bulk_items)

        stub = client.post(
            "/api/v1/llm/providers",
            headers=headers,
            json={
                "code": "det",
                "display_name": "Stub",
                "protocol": "deterministic",
                "base_url": "",
                "model": "deterministic-v1",
                "api_key": "",
            },
        )
        assert stub.status_code == 201
        probe = client.post(
            f"/api/v1/llm/providers/{stub.json()['data']['id']}/test",
            headers=headers,
            json={},
        )
        assert probe.status_code == 200
        assert probe.json()["data"]["ok"] is True

        denied = client.get("/api/v1/llm/providers")
        assert denied.status_code == 401


def test_non_admin_cannot_manage_llm(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    with TestClient(create_app(llm_config_path=str(tmp_path / "llm_config.json"))) as client:
        response = client.post(
            "/api/v1/llm/providers",
            json={
                "code": "x",
                "display_name": "x",
                "protocol": "deterministic",
                "model": "deterministic-v1",
            },
        )
        assert response.status_code == 401


def test_llm_providers_api_cursor_pages_and_filter_whitelist(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    with TestClient(create_app(llm_config_path=str(tmp_path / "llm_config.json"))) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        assert client.get("/api/v1/llm/providers?unexpected=1", headers=headers).status_code == 422
        assert client.get("/api/v1/llm/providers?cursor=bad", headers=headers).status_code == 400

        created_ids: list[str] = []
        for index in range(5):
            response = client.post(
                "/api/v1/llm/providers",
                headers=headers,
                json={
                    "code": f"page_provider_{index:02d}",
                    "display_name": f"Provider {index}",
                    "protocol": "deterministic",
                    "model": "deterministic-v1",
                    "is_default": index == 0,
                },
            )
            assert response.status_code == 201, response.text
            created_ids.append(response.json()["data"]["id"])

        first = client.get("/api/v1/llm/providers?limit=2", headers=headers)
        second = client.get(
            "/api/v1/llm/providers",
            params={"limit": 2, "cursor": first.json()["meta"]["next_cursor"]},
            headers=headers,
        )
        third = client.get(
            "/api/v1/llm/providers",
            params={"limit": 2, "cursor": second.json()["meta"]["next_cursor"]},
            headers=headers,
        )

        ids = [
            item["id"]
            for response in (first, second, third)
            for item in response.json()["data"]
        ]
        assert len(ids) == len(set(ids))
        assert set(created_ids) == set(ids)
        assert third.json()["meta"]["next_cursor"] is None
        assert all("api_key" not in item for item in first.json()["data"])
        assert all(item["api_key_hint"] in {"", "configured"} for item in first.json()["data"])


def test_memory_repository_persists_llm_config(tmp_path) -> None:
    path = tmp_path / "llm_config.json"
    first = InMemoryRepository(llm_config_path=str(path))
    secrets = SecretBox.from_settings()
    config = create_provider(
        first,
        secrets,
        code="persist_me",
        display_name="Persist",
        protocol="deterministic",
        base_url="",
        model="deterministic-v1",
        api_key="",
        is_default=True,
    )
    upsert_binding(first, agent_key="synthesize", provider_id=config.id)
    assert path.is_file()

    second = InMemoryRepository(llm_config_path=str(path))
    restored = second.get_llm_provider_by_code("persist_me")
    assert restored is not None
    assert restored.display_name == "Persist"
    assert second.get_llm_agent_binding("synthesize") is not None
    assert second.get_llm_agent_binding("synthesize").provider_id == config.id
