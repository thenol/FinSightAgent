from fastapi.testclient import TestClient

from app.agents.configuration import (
    create_prompt_version,
    get_agent_configuration,
    prompt_for_operation,
    publish_prompt_version,
    save_runtime_config,
    validate_prompt_version,
)
from app.main import create_app
from app.platform.repository import InMemoryRepository


def test_prompt_version_requires_validation_and_is_used_after_publish() -> None:
    repository = InMemoryRepository()
    created = create_prompt_version(
        repository,
        "fact_check",
        system_prompt="你是严谨的事实核验研究员，只基于经过验证的证据输出结论。",
        instruction_appendix="标记不确定性。",
        change_note="improve evidence discipline",
        actor_id="admin",
    )
    version_id = created.config["prompt_versions"][0]["id"]
    _, validation = validate_prompt_version(repository, "fact_check", version_id)
    assert validation["ok"] is True
    publish_prompt_version(repository, "fact_check", version_id, "admin")

    prompt, timeout, active_id = prompt_for_operation(
        repository, "fact_check", "Respond with valid JSON only.", 15
    )
    assert active_id == version_id
    assert "事实核验研究员" in prompt
    assert "不可移除的运行约束" in prompt
    assert timeout == 15


def test_agent_runtime_config_can_disable_operation() -> None:
    repository = InMemoryRepository()
    save_runtime_config(repository, "synthesize", enabled=False, timeout_seconds=20)
    current = get_agent_configuration(repository, "synthesize")
    assert current.config["enabled"] is False
    assert current.config["timeout_seconds"] == 20


def test_admin_can_create_validate_and_publish_prompt_version(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    with TestClient(create_app()) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        )
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        assert client.get("/api/v1/admin/agents", headers=headers).status_code == 200
        draft = client.post(
            "/api/v1/admin/agents/fact_check/prompt-versions",
            headers=headers,
            json={
                "system_prompt": "你是严谨的事实核验研究员，只根据证据输出可验证的中文结论。",
                "instruction_appendix": "明确事实与假设。",
                "change_note": "test draft",
            },
        )
        assert draft.status_code == 200
        version_id = draft.json()["data"]["prompt_versions"][0]["id"]
        assert client.post(
            f"/api/v1/admin/agents/fact_check/prompt-versions/{version_id}/validate",
            headers=headers,
        ).json()["data"]["validation"]["ok"] is True
        published = client.post(
            f"/api/v1/admin/agents/fact_check/prompt-versions/{version_id}/publish",
            headers=headers,
        )
        assert published.status_code == 200
        assert published.json()["data"]["published_prompt_version_id"] == version_id
