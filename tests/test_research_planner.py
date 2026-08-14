from datetime import datetime, timedelta, timezone

import pytest

from app.agents.registry import AgentRegistry
from app.model_gateway.service import DeterministicProvider, ModelGateway
from app.workflows.planner import PlanningError, ResearchPlanner


def test_classify_company_event():
    planner = ResearchPlanner()
    assert planner.classify_question("某公司净利润增长 50% 的影响") == "company_event"


def test_classify_macro_policy():
    planner = ResearchPlanner()
    assert planner.classify_question("美联储加息 25BP 对 A 股的影响") == "macro_policy"


def test_classify_market_risk():
    planner = ResearchPlanner()
    assert planner.classify_question("流动性风险对成长股的影响") == "market_risk"


def test_classify_general():
    planner = ResearchPlanner()
    assert planner.classify_question("人工智能发展趋势") == "general"


def test_create_plan_company_event():
    planner = ResearchPlanner(registry=AgentRegistry())
    plan = planner.create_plan(
        workflow_id="wfr_test",
        question="分析某公司业绩预告对净利润的影响",
        as_of=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert plan.status == "ready"
    assert plan.workflow_id == "wfr_test"
    task_names = {t.name for t in plan.tasks}
    assert "retrieve" in task_names
    assert "fact_verify" in task_names
    assert "company_analyze" in task_names
    assert "skeptic_review" in task_names
    assert "synthesize" in task_names


def test_create_plan_macro_policy():
    planner = ResearchPlanner(registry=AgentRegistry())
    plan = planner.create_plan(
        workflow_id="wfr_test",
        question="美联储加息 25BP 对银行板块的影响",
        as_of=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    task_names = {t.name for t in plan.tasks}
    assert "impact_analyze" in task_names
    assert "company_analyze" not in task_names


def test_plan_rejects_future_as_of():
    planner = ResearchPlanner()
    future = datetime.now(timezone.utc) + timedelta(days=1)
    with pytest.raises(PlanningError, match="AS_OF_IN_FUTURE"):
        planner.create_plan(
            workflow_id="wfr_test",
            question="测试问题",
            as_of=future,
        )


def test_plan_dependencies_are_valid():
    planner = ResearchPlanner(registry=AgentRegistry())
    plan = planner.create_plan(
        workflow_id="wfr_test",
        question="某公司发布业绩预告",
    )
    task_map = {t.name: t for t in plan.tasks}
    for task in plan.tasks:
        for dep in task.dependencies:
            assert dep in task_map, f"{task.name} depends on unknown {dep}"


def test_plan_has_required_tasks():
    planner = ResearchPlanner(registry=AgentRegistry())
    plan = planner.create_plan(
        workflow_id="wfr_test",
        question="某公司发布业绩预告",
    )
    assert plan.completion_criteria["required_tasks"]
    assert all(t.required for t in plan.tasks)


def test_llm_planner_deterministic_fallback():
    """确定性 Provider 应返回空调整，不破坏规则模板。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        gateway = ModelGateway(repository, provider=DeterministicProvider())
        planner = ResearchPlanner(registry=AgentRegistry(repository), model_gateway=gateway)
        plan = planner.create_plan(
            workflow_id="wfr_test",
            question="某公司发布业绩预告",
            use_llm=True,
        )
        assert plan.status == "ready"
        task_names = {t.name for t in plan.tasks}
        assert "retrieve" in task_names
        assert "synthesize" in task_names


def test_llm_planner_unauthorized_agent_is_rejected():
    """LLM 建议新增未注册 Agent 时应被过滤。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    class _BadProvider(DeterministicProvider):
        def invoke(self, request):
            return {
                "adjustments": [
                    {
                        "name": "bad_task",
                        "action": "add",
                        "agent_key": "unknown_agent",
                        "description": "未注册 Agent",
                    }
                ]
            }

    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        gateway = ModelGateway(repository, provider=_BadProvider())
        planner = ResearchPlanner(registry=AgentRegistry(repository), model_gateway=gateway)
        plan = planner.create_plan(
            workflow_id="wfr_test",
            question="某公司发布业绩预告",
            use_llm=True,
        )
        assert "bad_task" not in {t.name for t in plan.tasks}


def test_llm_planner_modify_description():
    """LLM 修改任务描述应被应用。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    class _ModifyProvider(DeterministicProvider):
        def invoke(self, request):
            return {
                "adjustments": [
                    {
                        "name": "retrieve",
                        "action": "modify",
                        "description": "自定义检索",
                    }
                ]
            }

    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        gateway = ModelGateway(repository, provider=_ModifyProvider())
        planner = ResearchPlanner(registry=AgentRegistry(repository), model_gateway=gateway)
        plan = planner.create_plan(
            workflow_id="wfr_test",
            question="某公司发布业绩预告",
            use_llm=True,
        )
        retrieve = next(t for t in plan.tasks if t.name == "retrieve")
        assert retrieve.description == "自定义检索"
