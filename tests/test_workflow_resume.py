"""工作流 fact_only 降级、waiting_review 与审核恢复。"""

from datetime import datetime, timezone

from app.domain import Claim, Event, ReviewTask
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository
from app.workflows.budget import BudgetManager, BudgetProfile
from app.workflows.invalidation import apply_invalidation, nodes_to_invalidate
from app.workflows.service import WorkflowService


def _event(event_id: str, importance: float = 0.5) -> Event:
    return Event(
        id=event_id,
        event_type="earnings_guidance",
        status="triaged",
        title="测试事件",
        entity_ids=[],
        document_ids=[],
        importance=importance,
        urgency="normal",
        occurred_at=datetime.now(timezone.utc),
    )


def _verified_claim(event_id: str) -> Claim:
    return Claim(
        id=new_id("clm"),
        event_id=event_id,
        subject_text="000001.SZ",
        predicate="document_discloses_event",
        object_value={"type": "string", "value": "earnings_guidance"},
        status="verified",
        confidence=0.9,
        evidence_ids=[new_id("evd")],
        as_of=datetime.now(timezone.utc),
    )


def test_invalidation_map_clears_downstream_fields() -> None:
    nodes = nodes_to_invalidate("claim_changed")
    assert nodes == ["company", "skeptic", "synthesize", "draft", "guardrail"]
    blackboard = {
        "event_snapshot": {"id": "e"},
        "fact_check_snapshot": {"ok": True},
        "company_analysis": {"direction": "positive"},
        "synthesis": {"status": "complete"},
    }
    cleared = apply_invalidation(blackboard, nodes)
    assert "event_snapshot" in cleared
    assert "fact_check_snapshot" in cleared
    assert "company_analysis" not in cleared
    assert "synthesis" not in cleared


def test_budget_hard_limit_with_verified_claim_degrades_to_fact_card() -> None:
    repository = InMemoryRepository()
    repository.save_event(_event("evt_degrade"))
    repository.save_claim(_verified_claim("evt_degrade"))
    tiny = BudgetProfile(
        name="tiny",
        limits={
            "model_calls": 1,
            "tool_calls": 1,
            "input_tokens": 1000,
            "output_tokens": 1000,
            "cost_minor_units": 10,
            "elapsed_seconds": 10,
        },
        node_limits={},
    )
    service = WorkflowService(repository, budget_manager=BudgetManager(repository, tiny))
    run = service.create("evt_degrade", "manual")
    result = service.run(run.id)

    assert result.status == "succeeded"
    assert result.blackboard.get("degraded_mode") == "fact_only"
    assert result.blackboard.get("synthesis", {}).get("status") == "fact_only"
    assert result.blackboard.get("report_draft", {}).get("report_type") == "fact_card"
    assert repository.list_review_tasks("pending") == []


def test_budget_hard_limit_without_claims_enters_waiting_review() -> None:
    repository = InMemoryRepository()
    repository.save_event(_event("evt_review", importance=0.9))
    tiny = BudgetProfile(
        name="tiny",
        limits={
            "model_calls": 1,
            "tool_calls": 1,
            "input_tokens": 1000,
            "output_tokens": 1000,
            "cost_minor_units": 10,
            "elapsed_seconds": 10,
        },
        node_limits={},
    )
    service = WorkflowService(repository, budget_manager=BudgetManager(repository, tiny))
    run = service.create("evt_review", "manual")
    result = service.run(run.id)

    assert result.status == "waiting_review"
    assert "BUDGET" in (result.error_code or "")
    tasks = repository.list_review_tasks("pending")
    assert len(tasks) == 1
    assert tasks[0].object_type == "workflow"
    assert tasks[0].object_id == run.id
    assert "downgrade_to_fact_card" in tasks[0].allowed_decisions


def test_resume_downgrade_to_fact_card() -> None:
    repository = InMemoryRepository()
    repository.save_event(_event("evt_down"))
    repository.save_claim(_verified_claim("evt_down"))
    service = WorkflowService(repository)
    run = service.create("evt_down", "manual")
    # 直接置为 waiting_review
    from dataclasses import replace

    waiting = replace(
        run,
        status="waiting_review",
        error_code="BUDGET_HARD_LIMIT",
        blackboard={"event_snapshot": {"id": "evt_down"}},
    )
    repository.update_workflow_run(waiting)
    result = service.resume(
        waiting.id, trigger="downgrade_fact_only", force_fact_only=True, reason="manual"
    )
    assert result.status == "succeeded"
    assert result.blackboard["degraded_mode"] == "fact_only"
    assert result.blackboard["report_draft"]["report_type"] == "fact_card"


def test_resume_approve_with_budget_adjust_and_invalidation() -> None:
    repository = InMemoryRepository()
    repository.save_event(_event("evt_resume"))
    repository.save_claim(_verified_claim("evt_resume"))
    service = WorkflowService(repository)
    run = service.create("evt_resume", "manual")
    # 先完整跑通，再模拟 waiting_review + 失效 company 下游后恢复
    succeeded = service.run(run.id)
    assert succeeded.status == "succeeded"
    from dataclasses import replace

    waiting = replace(
        succeeded,
        status="waiting_review",
        error_code="POLICY_VIOLATION",
        blackboard=dict(succeeded.blackboard),
    )
    repository.update_workflow_run(waiting)
    company_before = [
        a
        for a in repository.list_node_attempts(run.id, "company")
        if a.status == "succeeded"
    ]
    assert company_before

    resumed = service.resume(
        run.id,
        trigger="company_returned",
        resume_from="company",
        budget_adjust={"model_calls": 20, "tool_calls": 40},
        reason="approve resume",
    )
    assert resumed.status == "succeeded"
    invalidated = [
        a
        for a in repository.list_node_attempts(run.id, "company")
        if a.status == "invalidated"
    ]
    assert invalidated
    new_company = [
        a
        for a in repository.list_node_attempts(run.id, "company")
        if a.status == "succeeded" and a.attempt_no > company_before[0].attempt_no
    ]
    assert new_company
    # 预算 adjust 已入账
    assert any(e.entry_type == "adjust" for e in repository.list_budget_ledger(run.id))


def test_review_task_fields_roundtrip_in_memory() -> None:
    repository = InMemoryRepository()
    task = ReviewTask(
        id="rvt_1",
        object_type="workflow",
        object_id="wfr_1",
        reason_code="BUDGET_HARD_LIMIT",
        allowed_decisions=["approve", "reject"],
        resume_from="company",
        blackboard_version=3,
        created_at=datetime.now(timezone.utc),
    )
    repository.save_review_task(task)
    loaded = repository.get_review_task("rvt_1")
    assert loaded is not None
    assert loaded.resume_from == "company"
    assert loaded.blackboard_version == 3
