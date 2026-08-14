"""ResearchPlanner：基于研究问题生成动态 ResearchPlan（DD-80 §6）。

MVP 采用规则模板为主、LLM 可选增强的策略。Planner 不直接调用工具，只输出
受 Schema 约束的计划结构；Supervisor（执行引擎）拥有最终执行决定权。
"""

import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain import ResearchPlan, ResearchTask
from app.platform.ids import new_id

# 问题类型 -> 默认任务 DAG 模板
DEFAULT_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "company_event": [
        {
            "name": "retrieve",
            "agent_key": "retriever",
            "description": "检索与问题相关的文档、事件和证据块",
            "dependencies": [],
            "required": True,
            "input_fields": ["question", "as_of"],
            "output_field": "retrieve",
        },
        {
            "name": "fact_verify",
            "agent_key": "fact_checker",
            "description": "核验检索到的事实声明",
            "dependencies": ["retrieve"],
            "required": True,
            "input_fields": ["retrieve"],
            "output_field": "fact_verify",
        },
        {
            "name": "company_analyze",
            "agent_key": "company_analyst",
            "description": "公司基本面影响分析",
            "dependencies": ["fact_verify"],
            "required": True,
            "input_fields": ["fact_verify"],
            "output_field": "company_analyze",
        },
        {
            "name": "skeptic_review",
            "agent_key": "skeptic",
            "description": "反方审查与脆弱假设识别",
            "dependencies": ["company_analyze"],
            "required": True,
            "input_fields": ["company_analyze", "fact_verify"],
            "output_field": "skeptic_review",
        },
        {
            "name": "synthesize",
            "agent_key": "synthesizer",
            "description": "综合已有分析形成结论",
            "dependencies": ["skeptic_review"],
            "required": True,
            "input_fields": ["fact_verify", "company_analyze", "skeptic_review"],
            "output_field": "synthesize",
        },
    ],
    "macro_policy": [
        {
            "name": "retrieve",
            "agent_key": "retriever",
            "description": "检索宏观政策相关事件与文档",
            "dependencies": [],
            "required": True,
            "input_fields": ["question", "as_of"],
            "output_field": "retrieve",
        },
        {
            "name": "fact_verify",
            "agent_key": "fact_checker",
            "description": "核验政策事实",
            "dependencies": ["retrieve"],
            "required": True,
            "input_fields": ["retrieve"],
            "output_field": "fact_verify",
        },
        {
            "name": "impact_analyze",
            "agent_key": "impact_analyst",
            "description": "宏观/行业传导影响分析",
            "dependencies": ["fact_verify"],
            "required": True,
            "input_fields": ["fact_verify"],
            "output_field": "impact_analyze",
        },
        {
            "name": "synthesize",
            "agent_key": "synthesizer",
            "description": "综合形成结论",
            "dependencies": ["impact_analyze"],
            "required": True,
            "input_fields": ["fact_verify", "impact_analyze"],
            "output_field": "synthesize",
        },
    ],
    "market_risk": [
        {
            "name": "retrieve",
            "agent_key": "retriever",
            "description": "检索市场风险相关事件",
            "dependencies": [],
            "required": True,
            "input_fields": ["question", "as_of"],
            "output_field": "retrieve",
        },
        {
            "name": "fact_verify",
            "agent_key": "fact_checker",
            "description": "核验风险事实",
            "dependencies": ["retrieve"],
            "required": True,
            "input_fields": ["retrieve"],
            "output_field": "fact_verify",
        },
        {
            "name": "synthesize",
            "agent_key": "synthesizer",
            "description": "综合风险判断",
            "dependencies": ["fact_verify"],
            "required": True,
            "input_fields": ["fact_verify"],
            "output_field": "synthesize",
        },
    ],
    "general": [
        {
            "name": "retrieve",
            "agent_key": "retriever",
            "description": "检索相关背景信息",
            "dependencies": [],
            "required": True,
            "input_fields": ["question", "as_of"],
            "output_field": "retrieve",
        },
        {
            "name": "fact_verify",
            "agent_key": "fact_checker",
            "description": "核验关键事实",
            "dependencies": ["retrieve"],
            "required": True,
            "input_fields": ["retrieve"],
            "output_field": "fact_verify",
        },
        {
            "name": "synthesize",
            "agent_key": "synthesizer",
            "description": "综合回答",
            "dependencies": ["fact_verify"],
            "required": True,
            "input_fields": ["fact_verify"],
            "output_field": "synthesize",
        },
    ],
}


class PlanningError(ValueError):
    """计划生成失败或校验未通过。"""


class ResearchPlanner:
    """基于规则和可选 LLM 生成 ResearchPlan。"""

    def __init__(
        self,
        registry=None,
        model_gateway=None,
        templates: Optional[dict[str, list[dict[str, Any]]]] = None,
    ) -> None:
        self.registry = registry
        self.model_gateway = model_gateway
        self.templates = templates or DEFAULT_TEMPLATES

    _MACRO_KEYWORDS = ("加息", "降息", "lpr", "准备金", "fed", "fomc", "央行", "货币政策")
    _COMPANY_KEYWORDS = ("业绩", "净利润", "营收", "合同", "中标", "并购", "收购", "股东减持")
    _RISK_KEYWORDS = ("风险", "波动", "流动性", "危机", "地缘", "制裁")

    def classify_question(self, question: str) -> str:
        """规则化问题分类。"""
        q = question.lower()
        if any(k in q for k in self._MACRO_KEYWORDS):
            return "macro_policy"
        if any(k in q for k in self._COMPANY_KEYWORDS):
            return "company_event"
        if any(k in q for k in self._RISK_KEYWORDS):
            return "market_risk"
        return "general"

    def parse_as_of(self, question: str, default_as_of: Optional[datetime] = None) -> datetime:
        """从问题中抽取显式 as_of；MVP 仅支持 ISO 日期子串。"""
        iso_match = re.search(r"(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2})?)", question)
        if iso_match:
            value = iso_match.group(1).replace(" ", "T")
            if "T" not in value:
                value = f"{value}T00:00:00"
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
        return default_as_of or datetime.now(timezone.utc)

    def create_plan(
        self,
        workflow_id: str,
        question: str,
        as_of: Optional[datetime] = None,
        event_id: Optional[str] = None,
        budget_profile: str = "mvp_standard",
        use_llm: bool = False,
    ) -> ResearchPlan:
        """生成 ResearchPlan，包含任务 DAG 和完成标准。"""
        question_type = self.classify_question(question)
        effective_as_of = as_of or self.parse_as_of(question)
        now = datetime.now(timezone.utc)
        if effective_as_of > now:
            raise PlanningError("AS_OF_IN_FUTURE")

        template = self.templates.get(question_type, self.templates["general"])
        plan_id = new_id("rpl")
        task_map: dict[str, ResearchTask] = {}
        tasks: list[ResearchTask] = []
        for item in template:
            task = ResearchTask(
                id=new_id("rts"),
                plan_id=plan_id,
                name=item["name"],
                agent_key=item["agent_key"],
                description=item["description"],
                dependencies=list(item.get("dependencies", [])),
                required=item.get("required", True),
                status="pending",
                input_fields=list(item.get("input_fields", [])),
                output_field=item.get("output_field") or item["name"],
                output_schema=self._output_schema_for_agent(item["agent_key"]),
                created_at=now,
            )
            task_map[task.name] = task
            tasks.append(task)

        self._validate_dag(task_map)

        # 可选 LLM 增强：仅在注册表存在 planner Agent 时启用
        if use_llm and self.model_gateway and self.registry and self.registry.get("planner"):
            tasks = self._apply_llm_suggestions(
                question, question_type, tasks, event_id=event_id
            )
            self._validate_dag({t.name: t for t in tasks})

        metadata: dict[str, Any] = {"question_type": question_type}
        if event_id:
            metadata["event_id"] = event_id

        return ResearchPlan(
            id=plan_id,
            workflow_id=workflow_id,
            question=question,
            objective=self._derive_objective(question, question_type),
            as_of=effective_as_of,
            status="ready",
            tasks=tasks,
            budget_profile=budget_profile,
            completion_criteria={
                "required_tasks": [t.name for t in tasks if t.required],
            },
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )

    def _output_schema_for_agent(self, agent_key: str) -> Optional[str]:
        if self.registry is None:
            return None
        registration = self.registry.get(agent_key)
        return registration.output_schema_ref if registration else None

    def _validate_dag(self, task_map: dict[str, ResearchTask]) -> None:
        """检测任务依赖中缺失的节点和环。"""
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def _visit(name: str) -> None:
            if name not in task_map:
                raise PlanningError(f"UNKNOWN_TASK_DEPENDENCY:{name}")
            if name in rec_stack:
                raise PlanningError(f"CYCLIC_DEPENDENCY:{name}")
            if name in visited:
                return
            rec_stack.add(name)
            for dep in task_map[name].dependencies:
                _visit(dep)
            rec_stack.remove(name)
            visited.add(name)

        for name in task_map:
            _visit(name)

    def _apply_llm_suggestions(
        self,
        question: str,
        question_type: str,
        tasks: list[ResearchTask],
        event_id: Optional[str] = None,
    ) -> list[ResearchTask]:
        """调用 planner Agent 对任务列表做增删改建议，并在 Schema 内应用。"""
        # MVP：仅允许 LLM 调整任务描述和可选标志，禁止新增未注册 Agent、禁止修改依赖。
        # 真实 LLM 调用后续接入；当前返回原任务列表，保持可测试性。
        return [
            replace(t, description=t.description + f" [{question_type}]")
            for t in tasks
        ]

    def _derive_objective(self, question: str, question_type: str) -> str:
        type_label = {
            "company_event": "公司事件影响",
            "macro_policy": "宏观政策传导",
            "market_risk": "市场风险研判",
            "general": "一般研究问题",
        }.get(question_type, "研究问题")
        return f"[{type_label}] {question[:120]}"
