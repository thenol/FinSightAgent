"""File-backed LLM provider/bindings store for memory-mode development."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain import LlmAgentBinding, LlmProviderConfig


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _dump_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def provider_to_dict(config: LlmProviderConfig) -> dict[str, Any]:
    return {
        "id": config.id,
        "code": config.code,
        "display_name": config.display_name,
        "protocol": config.protocol,
        "base_url": config.base_url,
        "api_key_encrypted": config.api_key_encrypted,
        "model": config.model,
        "status": config.status,
        "is_default": config.is_default,
        "timeout_seconds": config.timeout_seconds,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "extra_config": config.extra_config,
        "created_at": _dump_dt(config.created_at),
        "updated_at": _dump_dt(config.updated_at),
    }


def provider_from_dict(data: dict[str, Any]) -> LlmProviderConfig:
    return LlmProviderConfig(
        id=data["id"],
        code=data["code"],
        display_name=data["display_name"],
        protocol=data["protocol"],
        base_url=data.get("base_url") or "",
        api_key_encrypted=data.get("api_key_encrypted") or "",
        model=data["model"],
        status=data.get("status") or "active",
        is_default=bool(data.get("is_default")),
        timeout_seconds=float(data.get("timeout_seconds") or 30),
        max_tokens=int(data.get("max_tokens") or 2048),
        temperature=float(data.get("temperature") or 0.2),
        extra_config=data.get("extra_config") or {},
        created_at=_parse_dt(data.get("created_at")),
        updated_at=_parse_dt(data.get("updated_at")),
    )


def binding_to_dict(binding: LlmAgentBinding) -> dict[str, Any]:
    return {
        "agent_key": binding.agent_key,
        "provider_id": binding.provider_id,
        "model_override": binding.model_override,
        "updated_at": _dump_dt(binding.updated_at),
    }


def binding_from_dict(data: dict[str, Any]) -> LlmAgentBinding:
    return LlmAgentBinding(
        agent_key=data["agent_key"],
        provider_id=data.get("provider_id"),
        model_override=data.get("model_override"),
        updated_at=_parse_dt(data.get("updated_at")),
    )


def load_llm_store(path: Path) -> tuple[dict[str, LlmProviderConfig], dict[str, LlmAgentBinding]]:
    if not path.is_file():
        return {}, {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    providers = {
        item["id"]: provider_from_dict(item) for item in raw.get("providers", []) if item.get("id")
    }
    bindings = {
        item["agent_key"]: binding_from_dict(item)
        for item in raw.get("bindings", [])
        if item.get("agent_key")
    }
    return providers, bindings


def save_llm_store(
    path: Path,
    providers: dict[str, LlmProviderConfig],
    bindings: dict[str, LlmAgentBinding],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "providers": [
            provider_to_dict(item)
            for item in sorted(providers.values(), key=lambda x: x.code)
        ],
        "bindings": [
            binding_to_dict(item) for item in sorted(bindings.values(), key=lambda x: x.agent_key)
        ],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
