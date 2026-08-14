from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.agents.registry import AgentRegistry
from app.domain import Event
from app.main import create_app
from app.platform.ids import new_id
from app.workflows.dynamic import DynamicWorkflowService


def _repository():
    with TestClient(create_app()) as client:
        return client.app.state.repository


def test_create_plan():
    repo = _repository()
    service = DynamicWorkflowService(repo, registry=AgentRegistry(repo))
    run, plan = service.create_plan(
        question="某公司发布业绩预告",
        as_of=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert run.status == "ready"
    assert plan.status == "ready"
    assert plan.workflow_id == run.id
    assert len(plan.tasks) > 0


def test_execute_general_plan():
    repo = _repository()
    service = DynamicWorkflowService(repo, registry=AgentRegistry(repo))
    _, plan = service.create_plan(
        question="人工智能发展趋势",
        as_of=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    final = service.execute(plan.id)
    assert final.status == "succeeded"
    for task in final.tasks:
        assert task.status == "succeeded"


def test_execute_company_event_plan():
    repo = _repository()
    service = DynamicWorkflowService(repo, registry=AgentRegistry(repo))
    _, plan = service.create_plan(
        question="某公司净利润增长 50% 的影响",
        as_of=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    final = service.execute(plan.id)
    assert final.status == "succeeded"
    task_names = {t.name: t for t in final.tasks}
    assert task_names["retrieve"].status == "succeeded"
    assert task_names["synthesize"].status == "succeeded"


def test_execute_plan_with_event_id():
    repo = _repository()
    event = Event(
        id=new_id("evt"),
        event_type="macro_policy",
        status="active",
        title="美联储加息",
        entity_ids=[],
        document_ids=[],
        importance=0.8,
        urgency="high",
        occurred_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    repo.save_event(event)
    service = DynamicWorkflowService(repo, registry=AgentRegistry(repo))
    _, plan = service.create_plan(
        question="美联储加息影响",
        event_id=event.id,
        as_of=event.occurred_at,
    )
    final = service.execute(plan.id)
    assert final.status in {"succeeded", "failed", "waiting_review"}


def test_execute_unknown_plan():
    repo = _repository()
    service = DynamicWorkflowService(repo, registry=AgentRegistry(repo))
    with pytest.raises(KeyError, match="RESEARCH_PLAN_NOT_FOUND"):
        service.execute("rpl_missing")


def test_node_attempts_created():
    repo = _repository()
    service = DynamicWorkflowService(repo, registry=AgentRegistry(repo))
    _, plan = service.create_plan(
        question="某公司发布业绩预告",
        as_of=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    service.execute(plan.id)
    attempts = repo.list_node_attempts(plan.workflow_id)
    assert len(attempts) == len(plan.tasks)
    for attempt in attempts:
        assert attempt.node_name.startswith("dynamic:")
        assert attempt.status == "succeeded"


def test_blackboard_updated():
    repo = _repository()
    service = DynamicWorkflowService(repo, registry=AgentRegistry(repo))
    _, plan = service.create_plan(
        question="某公司发布业绩预告",
        as_of=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    service.execute(plan.id)
    run = repo.get_workflow_run(plan.workflow_id)
    assert "research_plan" in run.blackboard
    assert "task_outputs" in run.blackboard
    assert "synthesize" in run.blackboard["task_outputs"]
