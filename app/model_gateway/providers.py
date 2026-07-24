"""HTTP LLM providers (OpenAI-compatible + Anthropic)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.model_gateway.service import ModelRequest


class ProviderError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _user_prompt(request: ModelRequest) -> str:
    return (
        "You are a structured research assistant. "
        "Return a single JSON object only (no markdown).\n"
        f"operation={request.operation}\n"
        f"input_schema={request.input_schema_version}\n"
        f"output_schema={request.output_schema_version}\n"
        f"payload={json.dumps(request.payload, ensure_ascii=False)}"
    )


def _parse_json_content(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"content": text}


@dataclass
class OpenAICompatibleProvider:
    name: str
    model: str
    base_url: str
    api_key: str
    timeout_seconds: float = 30.0
    max_tokens: int = 2048
    temperature: float = 0.2
    estimated_cost_usd: float = 0.0

    def invoke(self, request: ModelRequest) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": "Respond with valid JSON only.",
                },
                {"role": "user", "content": _user_prompt(request)},
            ],
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError("MODEL_TIMEOUT", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("MODEL_HTTP_ERROR", str(exc)) from exc
        if response.status_code >= 400:
            raise ProviderError(
                "MODEL_PROVIDER_ERROR",
                f"status={response.status_code} body={response.text[:400]}",
            )
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("MODEL_RESPONSE_INVALID", str(data)[:400]) from exc
        parsed = _parse_json_content(str(content))
        parsed.setdefault("operation", request.operation)
        parsed.setdefault("input", request.payload)
        return parsed


@dataclass
class AnthropicProvider:
    name: str
    model: str
    base_url: str
    api_key: str
    timeout_seconds: float = 30.0
    max_tokens: int = 2048
    temperature: float = 0.2
    estimated_cost_usd: float = 0.0
    anthropic_version: str = "2023-06-01"

    def invoke(self, request: ModelRequest) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + "/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": _user_prompt(request)}],
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError("MODEL_TIMEOUT", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("MODEL_HTTP_ERROR", str(exc)) from exc
        if response.status_code >= 400:
            raise ProviderError(
                "MODEL_PROVIDER_ERROR",
                f"status={response.status_code} body={response.text[:400]}",
            )
        data = response.json()
        try:
            blocks = data["content"]
            content = "".join(
                block.get("text", "") for block in blocks if isinstance(block, dict)
            )
        except (KeyError, TypeError) as exc:
            raise ProviderError("MODEL_RESPONSE_INVALID", str(data)[:400]) from exc
        parsed = _parse_json_content(content)
        parsed.setdefault("operation", request.operation)
        parsed.setdefault("input", request.payload)
        return parsed
