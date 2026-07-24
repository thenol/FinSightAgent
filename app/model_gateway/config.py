"""Build providers from stored config and resolve per-agent bindings."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from app.domain import (
    LLM_AGENT_KEYS,
    LLM_PROTOCOLS,
    AuditLog,
    LlmAgentBinding,
    LlmProviderConfig,
)
from app.model_gateway.presets import LLM_PRESETS
from app.model_gateway.providers import AnthropicProvider, OpenAICompatibleProvider, ProviderError
from app.model_gateway.secrets import SecretBox, api_key_status_label
from app.model_gateway.service import DeterministicProvider, ModelProvider, ModelRequest
from app.platform.ids import new_id


class LlmConfigError(ValueError):
    pass


def build_provider(
    config: LlmProviderConfig,
    secrets: SecretBox,
    *,
    model_override: str | None = None,
) -> ModelProvider:
    model = model_override or config.model
    if config.protocol == "deterministic":
        return DeterministicProvider(
            name=config.code or "deterministic",
            model=model or "deterministic-v1",
        )
    try:
        api_key = secrets.decrypt(config.api_key_encrypted)
    except ValueError as exc:
        if str(exc) == "LLM_API_KEY_DECRYPT_FAILED":
            raise LlmConfigError("LLM_API_KEY_DECRYPT_FAILED") from exc
        raise
    if not api_key:
        raise LlmConfigError("LLM_API_KEY_MISSING")
    if config.protocol == "openai_compatible":
        if not config.base_url:
            raise LlmConfigError("LLM_BASE_URL_REQUIRED")
        return OpenAICompatibleProvider(
            name=config.code,
            model=model,
            base_url=config.base_url,
            api_key=api_key,
            timeout_seconds=config.timeout_seconds,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
    if config.protocol == "anthropic":
        return AnthropicProvider(
            name=config.code,
            model=model,
            base_url=config.base_url or "https://api.anthropic.com",
            api_key=api_key,
            timeout_seconds=config.timeout_seconds,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            anthropic_version=str(config.extra_config.get("anthropic_version") or "2023-06-01"),
        )
    raise LlmConfigError("LLM_PROTOCOL_UNSUPPORTED")


def resolve_provider_for_operation(
    repository,
    secrets: SecretBox,
    operation: str,
) -> ModelProvider:
    config, model_override = resolve_config_for_operation(repository, operation)
    if config is None:
        return DeterministicProvider()
    if config.status != "active":
        return DeterministicProvider()
    try:
        return build_provider(config, secrets, model_override=model_override)
    except LlmConfigError as exc:
        _audit_provider_fallback(
            repository,
            operation=operation,
            provider_id=config.id,
            provider_code=config.code,
            reason=str(exc),
        )
        return DeterministicProvider(
            name="deterministic-fallback",
            model=model_override or config.model or "deterministic-v1",
        )


def _audit_provider_fallback(
    repository,
    *,
    operation: str,
    provider_id: str,
    provider_code: str,
    reason: str,
) -> None:
    saver = getattr(repository, "save_audit_log", None)
    if not callable(saver):
        return
    try:
        saver(
            AuditLog(
                id=new_id("aud"),
                actor_id=None,
                action="llm.provider_fallback",
                object_type="llm_provider",
                object_id=provider_id,
                request_id=None,
                details={
                    "operation": operation,
                    "provider_code": provider_code,
                    "reason": reason,
                    "fallback": "deterministic-fallback",
                },
                created_at=datetime.now(timezone.utc),
            )
        )
    except Exception:  # noqa: BLE001 — fallback must not fail the workflow
        return


def resolve_config_for_operation(
    repository, operation: str
) -> tuple[LlmProviderConfig | None, str | None]:
    binding = repository.get_llm_agent_binding(operation)
    model_override = binding.model_override if binding else None
    provider_id = binding.provider_id if binding else None
    if provider_id:
        config = repository.get_llm_provider(provider_id)
        return config, model_override
    return repository.get_default_llm_provider(), model_override


def public_provider_view(config: LlmProviderConfig, secrets: SecretBox) -> dict[str, Any]:
    _ = secrets  # kept for call-site symmetry; listing never decrypts keys
    configured = bool(config.api_key_encrypted)
    return {
        "id": config.id,
        "code": config.code,
        "display_name": config.display_name,
        "protocol": config.protocol,
        "base_url": config.base_url,
        "model": config.model,
        "status": config.status,
        "is_default": config.is_default,
        "timeout_seconds": config.timeout_seconds,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "extra_config": config.extra_config,
        "api_key_configured": configured,
        "api_key_hint": api_key_status_label(configured),
        "created_at": config.created_at,
        "updated_at": config.updated_at,
    }


def create_provider(
    repository,
    secrets: SecretBox,
    *,
    code: str,
    display_name: str,
    protocol: str,
    base_url: str,
    model: str,
    api_key: str = "",
    is_default: bool = False,
    timeout_seconds: float = 30.0,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    extra_config: dict[str, Any] | None = None,
    status: str = "active",
) -> LlmProviderConfig:
    if protocol not in LLM_PROTOCOLS:
        raise LlmConfigError("LLM_PROTOCOL_UNSUPPORTED")
    if repository.get_llm_provider_by_code(code):
        raise LlmConfigError("LLM_PROVIDER_CODE_EXISTS")
    if protocol != "deterministic" and not api_key:
        raise LlmConfigError("LLM_API_KEY_REQUIRED")
    now = datetime.now(timezone.utc)
    config = LlmProviderConfig(
        id=new_id("llm"),
        code=code,
        display_name=display_name,
        protocol=protocol,
        base_url=base_url.rstrip("/") if base_url else "",
        api_key_encrypted=secrets.encrypt(api_key) if api_key else "",
        model=model,
        status=status,
        is_default=is_default,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_config=extra_config or {},
        created_at=now,
        updated_at=now,
    )
    with repository.transaction() as tx:
        if is_default:
            _clear_default(tx)
        tx.save_llm_provider(config)
    return config


def update_provider(
    repository,
    secrets: SecretBox,
    provider_id: str,
    *,
    display_name: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    status: str | None = None,
    is_default: bool | None = None,
    timeout_seconds: float | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    extra_config: dict[str, Any] | None = None,
) -> LlmProviderConfig:
    existing = repository.get_llm_provider(provider_id)
    if not existing:
        raise LlmConfigError("LLM_PROVIDER_NOT_FOUND")
    # Non-rotate updates must not accept api_key here for clearer audit trails;
    # callers that still pass api_key via PATCH are supported but prefer rotate_provider_key.
    encrypted = existing.api_key_encrypted
    if api_key is not None and api_key != "":
        encrypted = secrets.encrypt(api_key)
    updated = replace(
        existing,
        display_name=display_name if display_name is not None else existing.display_name,
        base_url=base_url.rstrip("/") if base_url is not None else existing.base_url,
        model=model if model is not None else existing.model,
        api_key_encrypted=encrypted,
        status=status if status is not None else existing.status,
        is_default=is_default if is_default is not None else existing.is_default,
        timeout_seconds=timeout_seconds
        if timeout_seconds is not None
        else existing.timeout_seconds,
        max_tokens=max_tokens if max_tokens is not None else existing.max_tokens,
        temperature=temperature if temperature is not None else existing.temperature,
        extra_config=extra_config if extra_config is not None else existing.extra_config,
        updated_at=datetime.now(timezone.utc),
    )
    with repository.transaction() as tx:
        if updated.is_default:
            _clear_default(tx, except_id=updated.id)
        tx.update_llm_provider(updated)
    return updated


def rotate_provider_key(
    repository,
    secrets: SecretBox,
    provider_id: str,
    *,
    api_key: str,
) -> LlmProviderConfig:
    if not api_key:
        raise LlmConfigError("LLM_API_KEY_REQUIRED")
    existing = repository.get_llm_provider(provider_id)
    if not existing:
        raise LlmConfigError("LLM_PROVIDER_NOT_FOUND")
    if existing.protocol == "deterministic":
        raise LlmConfigError("LLM_DETERMINISTIC_HAS_NO_KEY")
    updated = replace(
        existing,
        api_key_encrypted=secrets.encrypt(api_key),
        updated_at=datetime.now(timezone.utc),
    )
    with repository.transaction() as tx:
        tx.update_llm_provider(updated)
    return updated


def upsert_binding(
    repository,
    *,
    agent_key: str,
    provider_id: str | None,
    model_override: str | None = None,
) -> LlmAgentBinding:
    if agent_key not in LLM_AGENT_KEYS:
        raise LlmConfigError("LLM_AGENT_KEY_INVALID")
    if provider_id:
        provider = repository.get_llm_provider(provider_id)
        if not provider:
            raise LlmConfigError("LLM_PROVIDER_NOT_FOUND")
    binding = LlmAgentBinding(
        agent_key=agent_key,
        provider_id=provider_id,
        model_override=model_override or None,
        updated_at=datetime.now(timezone.utc),
    )
    with repository.transaction() as tx:
        tx.upsert_llm_agent_binding(binding)
    return binding


def bind_all_agents(
    repository,
    *,
    provider_id: str | None,
    model_override: str | None = None,
) -> list[LlmAgentBinding]:
    if provider_id:
        provider = repository.get_llm_provider(provider_id)
        if not provider:
            raise LlmConfigError("LLM_PROVIDER_NOT_FOUND")
    now = datetime.now(timezone.utc)
    bindings = [
        LlmAgentBinding(
            agent_key=agent_key,
            provider_id=provider_id,
            model_override=model_override or None,
            updated_at=now,
        )
        for agent_key in sorted(LLM_AGENT_KEYS)
    ]
    with repository.transaction() as tx:
        for binding in bindings:
            tx.upsert_llm_agent_binding(binding)
    return bindings


def test_provider(
    repository,
    secrets: SecretBox,
    provider_id: str,
) -> dict[str, Any]:
    config = repository.get_llm_provider(provider_id)
    if not config:
        raise LlmConfigError("LLM_PROVIDER_NOT_FOUND")
    provider = build_provider(config, secrets)
    request = ModelRequest(
        operation="connectivity_probe",
        input_schema_version="v1",
        output_schema_version="v1",
        payload={"ping": True},
        timeout_seconds=min(config.timeout_seconds, 20.0),
        max_cost_usd=1.0,
    )
    try:
        output = provider.invoke(request)
    except ProviderError as exc:
        return {"ok": False, "error_code": exc.code, "detail": exc.detail[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_code": type(exc).__name__, "detail": str(exc)[:300]}
    return {
        "ok": True,
        "provider": config.code,
        "model": config.model,
        "sample_keys": sorted(output.keys())[:8],
    }


def list_presets() -> list[dict[str, Any]]:
    return list(LLM_PRESETS)


def _clear_default(tx, *, except_id: str | None = None) -> None:
    for item in tx.list_llm_providers():
        if item.is_default and item.id != except_id:
            tx.update_llm_provider(
                replace(item, is_default=False, updated_at=datetime.now(timezone.utc))
            )
