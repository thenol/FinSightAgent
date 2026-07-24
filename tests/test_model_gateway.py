import pytest

from app.model_gateway.service import DeterministicProvider, ModelGateway, ModelRequest
from app.platform.repository import InMemoryRepository


def test_gateway_persists_and_replays_versioned_request() -> None:
    gateway = ModelGateway(InMemoryRepository())
    request = ModelRequest(
        operation="classify",
        input_schema_version="v1",
        output_schema_version="v1",
        payload={"text": "公告"},
    )
    first = gateway.invoke(request)
    replay = gateway.invoke(request)
    assert first.payload["input"] == {"text": "公告"}
    assert replay.replayed is True
    assert replay.run_id == first.run_id


def test_gateway_enforces_budget_before_provider_call() -> None:
    gateway = ModelGateway(InMemoryRepository(), DeterministicProvider(estimated_cost_usd=0.01))
    request = ModelRequest(
        operation="classify",
        input_schema_version="v1",
        output_schema_version="v1",
        payload={},
        max_cost_usd=0,
    )
    with pytest.raises(ValueError, match="MODEL_BUDGET_EXCEEDED"):
        gateway.invoke(request)
