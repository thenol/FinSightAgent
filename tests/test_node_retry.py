"""节点重试退避与 attempt_no 递增。"""

from datetime import datetime, timezone

import pytest

from app.domain import Event
from app.platform.repository import InMemoryRepository
from app.workflows.errors import (
    TransientModelError,
    classify_error,
    max_retries_for,
    should_retry,
)
from app.workflows.service import ResearchState, WorkflowService


def test_classify_transient_and_non_retryable() -> None:
    assert classify_error(TransientModelError()) == "MODEL_TRANSIENT"
    assert classify_error(TimeoutError()) == "MODEL_TRANSIENT"
    assert max_retries_for("MODEL_TRANSIENT") == 2
    assert max_retries_for("OUTPUT_SCHEMA_INVALID") == 1
    assert should_retry("MODEL_TRANSIENT", 1) is True
    assert should_retry("MODEL_TRANSIENT", 2) is True
    assert should_retry("MODEL_TRANSIENT", 3) is False
    assert should_retry("TOOL_AS_OF_VIOLATION", 1) is False


def test_node_retries_transient_error_then_succeeds() -> None:
    repository = InMemoryRepository()
    repository.save_event(
        Event(
            id="evt_retry",
            event_type="earnings_guidance",
            status="triaged",
            title="retry",
            entity_ids=[],
            document_ids=[],
            importance=0.5,
            urgency="normal",
            occurred_at=datetime.now(timezone.utc),
        )
    )
    sleeps: list[float] = []
    service = WorkflowService(repository, sleep_fn=lambda s: sleeps.append(s))
    calls = {"n": 0}

    def flaky(_state: ResearchState) -> dict:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientModelError("MODEL_TRANSIENT")
        return {"event_snapshot": {"id": "evt_retry", "ok": True}}

    output = service._execute_node(
        "context",
        flaky,
        {"workflow_id": "wfr_retry", "event_id": "evt_retry", "as_of": "2026-07-01T00:00:00+00:00"},
        input_fields=(),
        reserve_amounts={"model_calls": 0, "tool_calls": 0, "elapsed_seconds": 1},
    )
    assert output["event_snapshot"]["ok"] is True
    assert calls["n"] == 3
    assert len(sleeps) == 2
    attempts = repository.list_node_attempts("wfr_retry", "context")
    assert [a.attempt_no for a in attempts] == [1, 2, 3]
    assert [a.status for a in attempts] == ["failed", "failed", "succeeded"]


def test_non_retryable_error_fails_immediately() -> None:
    repository = InMemoryRepository()
    service = WorkflowService(repository, sleep_fn=lambda _s: None)
    calls = {"n": 0}

    def boom(_state: ResearchState) -> dict:
        calls["n"] += 1
        raise ValueError("NODE_EXECUTION_ERROR")

    with pytest.raises(ValueError):
        service._execute_node(
            "context",
            boom,
            {"workflow_id": "wfr_once", "event_id": "evt", "as_of": "2026-07-01T00:00:00+00:00"},
            input_fields=(),
            reserve_amounts={"model_calls": 0, "tool_calls": 0, "elapsed_seconds": 1},
        )
    assert calls["n"] == 1
    attempts = repository.list_node_attempts("wfr_once", "context")
    assert len(attempts) == 1
    assert attempts[0].status == "failed"
