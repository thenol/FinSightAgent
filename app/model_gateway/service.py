import hashlib
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain import ModelRun
from app.model_gateway.secrets import SecretBox
from app.platform.ids import new_id


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str = Field(min_length=1, max_length=80)
    input_schema_version: str = Field(pattern=r"^v[0-9]+$")
    output_schema_version: str = Field(pattern=r"^v[0-9]+$")
    payload: dict[str, Any]
    timeout_seconds: float = Field(default=15, gt=0, le=120)
    max_cost_usd: float = Field(default=0, ge=0)
    system_prompt: str = Field(default="Respond with valid JSON only.", min_length=1)


class ModelResponse(BaseModel):
    run_id: str
    payload: dict[str, Any]
    replayed: bool = False


class ModelProvider(Protocol):
    name: str
    model: str
    estimated_cost_usd: float

    def invoke(self, request: ModelRequest) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DeterministicProvider:
    """Safe local provider for contract tests and offline replay."""

    name: str = "deterministic"
    model: str = "deterministic-v1"
    estimated_cost_usd: float = 0.0

    def invoke(self, request: ModelRequest) -> dict[str, Any]:
        if request.operation == "event_route":
            from app.events.router import deterministic_route_payload

            return deterministic_route_payload(request.payload)
        if request.operation == "plan":
            # 确定性 Planner：不修改任务，只返回空调整，确保离线/测试可运行
            return {
                "objective_refined": None,
                "adjustments": [],
                "reasoning": "deterministic fallback: no changes",
            }
        return {"operation": request.operation, "input": request.payload}


class ModelGateway:
    def __init__(
        self,
        repository,
        provider: Optional[ModelProvider] = None,
        *,
        secrets: Optional[SecretBox] = None,
    ) -> None:
        self.repository = repository
        self._override_provider = provider
        self.secrets = secrets or SecretBox.from_settings()

    @property
    def provider(self) -> ModelProvider:
        """Compatibility for tests that inspect gateway.provider."""
        return self._override_provider or DeterministicProvider()

    def invoke(self, request: ModelRequest) -> ModelResponse:
        active = self._resolve_provider(request.operation)
        request_hash = self._hash(request)
        prior = self.repository.find_model_run_by_hash(request_hash)
        if prior and prior.status == "succeeded" and prior.output_payload is not None:
            return ModelResponse(run_id=prior.id, payload=prior.output_payload, replayed=True)
        if active.estimated_cost_usd > request.max_cost_usd:
            raise ValueError("MODEL_BUDGET_EXCEEDED")
        started = time.perf_counter()
        try:
            output = active.invoke(request)
        except Exception as exc:
            self._save(
                request,
                request_hash,
                None,
                "failed",
                int((time.perf_counter() - started) * 1000),
                type(exc).__name__,
                active,
            )
            raise
        run = self._save(
            request,
            request_hash,
            output,
            "succeeded",
            int((time.perf_counter() - started) * 1000),
            None,
            active,
        )
        return ModelResponse(run_id=run.id, payload=output)

    def _resolve_provider(self, operation: str) -> ModelProvider:
        if self._override_provider is not None:
            return self._override_provider
        from app.model_gateway.config import resolve_provider_for_operation

        return resolve_provider_for_operation(self.repository, self.secrets, operation)

    def _save(self, request, request_hash, output, status, latency_ms, error_code, provider):
        run = ModelRun(
            id=new_id("mlr"),
            operation=request.operation,
            provider=provider.name,
            model=provider.model,
            input_schema_version=request.input_schema_version,
            output_schema_version=request.output_schema_version,
            request_hash=request_hash,
            input_payload=request.payload,
            output_payload=output,
            status=status,
            latency_ms=latency_ms,
            estimated_cost_usd=provider.estimated_cost_usd,
            error_code=error_code,
        )
        self.repository.save_model_run(run)
        return run

    @staticmethod
    def _hash(request: ModelRequest) -> str:
        return hashlib.sha256(
            request.model_dump_json(exclude={"timeout_seconds"}).encode()
        ).hexdigest()
