import pytest

from app.platform.repository import InMemoryRepository
from app.workflows.blackboard import (
    FIELD_OWNERS,
    BlackboardGuard,
    BlackboardOwnershipError,
    BlackboardVersionConflict,
)
from app.workflows.service import WorkflowService


def test_field_owners_cover_all_blackboard_fields() -> None:
    assert FIELD_OWNERS["company_analysis"] == "company"
    assert FIELD_OWNERS["counter_analysis"] == "skeptic"
    assert FIELD_OWNERS["synthesis"] == "synthesize"
    assert FIELD_OWNERS["fact_check_snapshot"] == "fact_check"
    assert FIELD_OWNERS["guardrail_result"] == "guardrail"


def test_guard_rejects_non_owner_write() -> None:
    """skeptic 试图写 company_analysis 字段应被拒。"""
    repository = InMemoryRepository()
    service = WorkflowService(repository)
    run = service.create("evt_bb", "manual")
    repository.update_workflow_run(
        type(run)(
            id=run.id,
            event_id=run.event_id,
            trigger_id=run.trigger_id,
            status="running",
            as_of=run.as_of,
            state_version=0,
        )
    )
    guard = BlackboardGuard(repository)

    with pytest.raises(BlackboardOwnershipError) as exc_info:
        guard.validate_write(run.id, "skeptic", {"company_analysis": {}}, expected_state_version=0)
    assert exc_info.value.field == "company_analysis"
    assert exc_info.value.owner == "company"


def test_guard_rejects_version_conflict() -> None:
    """expected_state_version 不匹配抛 BlackboardVersionConflict。"""
    repository = InMemoryRepository()
    service = WorkflowService(repository)
    run = service.create("evt_v", "manual")
    repository.update_workflow_run(
        type(run)(
            id=run.id,
            event_id=run.event_id,
            trigger_id=run.trigger_id,
            status="running",
            as_of=run.as_of,
            state_version=5,
        )
    )
    guard = BlackboardGuard(repository)

    with pytest.raises(BlackboardVersionConflict) as exc_info:
        guard.validate_write(run.id, "company", {"company_analysis": {}}, expected_state_version=3)
    assert exc_info.value.expected == 3
    assert exc_info.value.actual == 5


def test_guard_allows_owner_write_with_matching_version() -> None:
    repository = InMemoryRepository()
    service = WorkflowService(repository)
    run = service.create("evt_ok", "manual")
    repository.update_workflow_run(
        type(run)(
            id=run.id,
            event_id=run.event_id,
            trigger_id=run.trigger_id,
            status="running",
            as_of=run.as_of,
            state_version=0,
        )
    )
    guard = BlackboardGuard(repository)

    # 不抛异常即通过
    guard.validate_write(run.id, "company", {"company_analysis": {}}, expected_state_version=0)
    assert guard.next_state_version(run.id) == 1
