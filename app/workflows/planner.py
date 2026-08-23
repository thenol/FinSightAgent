"""ResearchPlanner：基于研究问题生成动态 ResearchPlan（DD-80 §6）。

MVP 采用规则模板为主、LLM 可选增强的策略。Planner 不直接调用工具，只输出
受 Schema 约束的计划结构；Supervisor（执行引擎）拥有最终执行决定权。
"""

import logging
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.domain import ResearchPlan, ResearchTask
from app.model_gateway.failures import record_model_failure
from app.model_gateway.service import ModelRequest
from app.platform.ids import new_id

logger = logging.getLogger(__name__)


class TaskAdjustment(BaseModel):
    """LLM 对单个任务的建议调整。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    action: str = Field(pattern="^(add|modify|remove)$")
    agent_key: Optional[str] = None
    description: Optional[str] = None
    dependencies: Optional[list[str]] = None
    required: Optional[bool] = None
    input_fields: Optional[list[str]] = None
    output_field: Optional[str] = None
    reason: str = Field(default="", max_length=500)


class PlanAdjustmentOutput(BaseModel):
    """LLM Planner 输出 Schema：只允许在规则模板基础上做受控调整。"""

    model_config = ConfigDict(extra="forbid")
    objective_refined: Optional[str] = Field(default=None, max_length=300)
    adjustments: list[TaskAdjustment] = Field(default_factory=list)
    reasoning: str = Field(default="", max_length=1000)

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
        if self.model_gateway is None:
            return tasks

        payload = {
            "question": question,
            "question_type": question_type,
            "event_id": event_id,
            "candidate_tasks": [
                {
                    "name": t.name,
                    "agent_key": t.agent_key,
                    "description": t.description,
                    "dependencies": t.dependencies,
                    "required": t.required,
                    "input_fields": t.input_fields,
                    "output_field": t.output_field,
                }
                for t in tasks
            ],
        }
        try:
            response = self.model_gateway.invoke(
                ModelRequest(
                    operation="plan",
                    input_schema_version="v1",
                    output_schema_version="v1",
                    payload=payload,
                    system_prompt=self._planner_system_prompt(),
                )
            )
            parsed = PlanAdjustmentOutput.model_validate(response.payload)
        except Exception as exc:
            record_model_failure(logger, operation="plan", stage="plan_adjust", exc=exc)
            return tasks

        task_map: dict[str, ResearchTask] = {t.name: t for t in tasks}
        for adjustment in parsed.adjustments:
            if adjustment.action == "modify" and adjustment.name in task_map:
                task = task_map[adjustment.name]
                # 只允许修改 description / required；依赖和 agent_key 由规则保证
                task_map[task.name] = replace(
                    task,
                    description=adjustment.description or task.description,
                    required=(
                        adjustment.required if adjustment.required is not None else task.required
                    ),
                )
            elif adjustment.action == "add":
                # 新增任务必须来自已注册 Agent 且名称不冲突
                if adjustment.agent_key is None:
                    continue
                if self.registry is None or self.registry.get(adjustment.agent_key) is None:
                    continue
                if adjustment.name in task_map:
                    continue
                task_map[adjustment.name] = ResearchTask(
                    id=new_id("rts"),
                    plan_id=task_map[next(iter(task_map))].plan_id if task_map else "",
                    name=adjustment.name,
                    agent_key=adjustment.agent_key,
                    description=adjustment.description or "",
                    dependencies=list(adjustment.dependencies or []),
                    required=adjustment.required if adjustment.required is not None else True,
                    status="pending",
                    input_fields=list(adjustment.input_fields or []),
                    output_field=adjustment.output_field or adjustment.name,
                    output_schema=self._output_schema_for_agent(adjustment.agent_key),
                    created_at=datetime.now(timezone.utc),
                )
            elif adjustment.action == "remove" and adjustment.name in task_map:
                # 不允许移除必需任务
                if not task_map[adjustment.name].required:
                    del task_map[adjustment.name]

        return list(task_map.values())

    @staticmethod
    def _planner_system_prompt() -> str:
        return (
            "你是一名金融研究规划专家。根据用户问题和默认任务模板，"
            "输出对任务列表的受控调整建议。只返回合法 JSON。"
            "adjustments 中每个元素包含 name、action（add/modify/remove）、"
            "可选 agent_key/description/dependencies/required/input_fields/output_field/reason。"
            "禁止引入未注册 Agent，禁止将必需任务设为可选，禁止产生循环依赖。"
        )

    def _derive_objective(self, question: str, question_type: str) -> str:
        type_label = {
            "company_event": "公司事件影响",
            "macro_policy": "宏观政策传导",
            "market_risk": "市场风险研判",
            "general": "一般研究问题",
        }.get(question_type, "研究问题")
        return f"[{type_label}] {question[:120]}"
