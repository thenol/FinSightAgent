"""动态研究工作流执行引擎（DD-80 §7）。

根据 ResearchPlan 的任务 DAG 进行拓扑调度，复用现有 WorkflowRun、Blackboard、
BudgetManager、NodeAttempt 和 ReviewTask 机制。每个任务调用对应的 Specialist Agent
执行器，输出写入 Blackboard 的 ``task_outputs``。
"""

import hashlib
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from app.analysis.service import ImpactAnalysisService
from app.domain import (
    NodeAttempt,
    ResearchPlan,
    ResearchTask,
    RetrievalRequest,
    ReviewTask,
    WorkflowRun,
)
from app.platform.asof import ensure_within_as_of
from app.platform.ids import new_id
from app.retrieval.service import RetrievalService
from app.workflows.agents import (
    CompanyAnalystAgent,
    FactCheckerAgent,
    SkepticAgent,
    SynthesizerAgent,
)
from app.workflows.budget import BudgetExceeded, BudgetManager, elapsed_since
from app.workflows.errors import (
    classify_error,
    compute_backoff_seconds,
    default_sleep,
    should_retry,
)


class DynamicWorkflowError(RuntimeError):
    """动态工作流执行异常。"""


class TaskExecutionError(RuntimeError):
    """单个任务执行失败且重试耗尽。"""


def _sha256(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class DynamicWorkflowService:
    """执行 ResearchPlan 的动态 DAG。"""

    def __init__(
        self,
        repository,
        registry=None,
        model_gateway=None,
        retrieval_service=None,
        budget_manager=None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.model_gateway = model_gateway
        self.retrieval_service = retrieval_service or RetrievalService(repository)
        self.budget = budget_manager or BudgetManager(repository)
        self._sleep = sleep_fn or default_sleep
        _Executor = Callable[[ResearchTask, dict[str, Any], WorkflowRun], dict[str, Any]]
        self._executors: dict[str, _Executor] = {
            "retriever": self._execute_retriever,
            "fact_checker": self._execute_fact_checker,
            "company_analyst": self._execute_company_analyst,
            "skeptic": self._execute_skeptic,
            "synthesizer": self._execute_synthesizer,
            "impact_analyst": self._execute_impact_analyst,
            "market_analyst": self._execute_market_analyst,
            "industry_analyst": self._execute_industry_analyst,
            "regulatory_analyst": self._execute_regulatory_analyst,
        }

    # --- public lifecycle ---

    def create_plan(
        self,
        question: str,
        as_of: datetime | None = None,
        event_id: str | None = None,
        budget_profile: str = "mvp_standard",
        trigger_id: str = "research_api",
    ) -> tuple[WorkflowRun, ResearchPlan]:
        """创建 WorkflowRun 并生成 ResearchPlan。"""
        from app.workflows.planner import ResearchPlanner

        effective_as_of = as_of or datetime.now(timezone.utc)
        if event_id:
            event = self.repository.get_event(event_id)
            ensure_within_as_of(event, effective_as_of, context="dynamic_workflow:create_plan")

        run = WorkflowRun(
            id=new_id("wfr"),
            event_id=event_id or "",
            trigger_id=trigger_id,
            status="pending",
            as_of=effective_as_of,
            created_at=datetime.now(timezone.utc),
            budget_profile=budget_profile,
        )
        self.repository.save_workflow_run(run)

        planner = ResearchPlanner(registry=self.registry, model_gateway=self.model_gateway)
        plan = planner.create_plan(
            workflow_id=run.id,
            question=question,
            as_of=effective_as_of,
            event_id=event_id,
            budget_profile=budget_profile,
            use_llm=self.model_gateway is not None,
        )
        self.repository.save_research_plan(plan)
        for task in plan.tasks:
            self.repository.save_research_task(task)

        # 把计划挂到 Blackboard
        self._update_blackboard(run.id, {"research_plan": self._plan_to_blackboard(plan)})
        updated_run = replace(
            self.repository.get_workflow_run(run.id),
            status="ready",
            state_version=self._state_version(run.id),
        )
        self.repository.update_workflow_run(updated_run)
        return updated_run, plan

    def execute(self, plan_id: str) -> ResearchPlan:
        """执行计划；返回最终状态。"""
        plan = self.repository.get_research_plan(plan_id)
        if plan is None:
            raise KeyError("RESEARCH_PLAN_NOT_FOUND")
        run = self.repository.get_workflow_run(plan.workflow_id)
        if run is None:
            raise KeyError("WORKFLOW_NOT_FOUND")

        self.repository.update_workflow_run(replace(run, status="running"))
        plan = replace(plan, status="running", updated_at=datetime.now(timezone.utc))
        self.repository.update_research_plan(plan)

        try:
            final_plan = self._run_loop(plan, run)
        except BudgetExceeded as exc:
            final_plan = self._handle_budget_exceeded(plan, run, exc)
        except DynamicWorkflowError as exc:
            final_plan = self._handle_failure(plan, run, str(exc))

        return final_plan

    # --- execution loop ---

    def _run_loop(self, plan: ResearchPlan, run: WorkflowRun) -> ResearchPlan:
        task_map = {t.name: t for t in plan.tasks}
        completed: set[str] = set()
        failed_required: list[str] = []

        while True:
            ready = self._ready_tasks(plan, completed)
            if not ready:
                break
            for task in ready:
                executed = self._execute_task(task, run, plan)
                task_map[task.name] = executed
                plan = replace(plan, tasks=list(task_map.values()))
                self.repository.update_research_task(executed)
                if executed.status == "succeeded":
                    completed.add(task.name)
                elif executed.status == "failed":
                    if executed.required:
                        failed_required.append(task.name)
                    else:
                        completed.add(task.name)  # 可选任务失败视为已完成
                elif executed.status == "waiting_review":
                    plan = replace(
                        plan,
                        status="waiting_review",
                        tasks=list(task_map.values()),
                        updated_at=datetime.now(timezone.utc),
                    )
                    self.repository.update_research_plan(plan)
                    return plan

        # 判断最终状态
        if failed_required:
            status = "failed"
        elif len(completed) >= len(
            [t for t in plan.tasks if t.required]
        ) and self._all_required_completed(plan, completed):
            status = "succeeded"
        else:
            status = "failed"

        plan = replace(
            plan,
            status=status,
            tasks=list(task_map.values()),
            updated_at=datetime.now(timezone.utc),
        )
        self.repository.update_research_plan(plan)
        fresh_run = self.repository.get_workflow_run(run.id)
        self.repository.update_workflow_run(
            replace(
                fresh_run,
                status=status if status != "waiting_review" else fresh_run.status,
                state_version=self._state_version(run.id),
            )
        )
        return plan

    def _ready_tasks(self, plan: ResearchPlan, completed: set[str]) -> list[ResearchTask]:
        ready: list[ResearchTask] = []
        for task in plan.tasks:
            if task.status not in {"pending", "ready"}:
                continue
            if set(task.dependencies).issubset(completed):
                ready.append(replace(task, status="ready"))
        return ready

    def _all_required_completed(self, plan: ResearchPlan, completed: set[str]) -> bool:
        return all(t.name in completed for t in plan.tasks if t.required)

    # --- single task execution ---

    def _execute_task(
        self, task: ResearchTask, run: WorkflowRun, plan: ResearchPlan
    ) -> ResearchTask:
        """执行单个任务：预算预留、幂等复用、重试、结算、写 Blackboard。"""
        workflow_id = run.id
        blackboard = self._blackboard(workflow_id)
        inputs = self._collect_inputs(task, blackboard)
        input_hash = _sha256(inputs)

        existing = self.repository.find_node_attempt(
            workflow_id, f"dynamic:{task.name}", input_hash
        )
        if existing is not None and existing.output is not None:
            return self._task_succeeded(task, existing.output)

        amounts = {"model_calls": 1, "tool_calls": 2, "elapsed_seconds": 1}
        failed_attempts = 0
        started = time.perf_counter()

        while True:
            self.budget.reserve(workflow_id, f"dynamic:{task.name}", amounts)
            attempt = NodeAttempt(
                id=new_id("nat"),
                workflow_id=workflow_id,
                node_name=f"dynamic:{task.name}",
                attempt_no=self._next_attempt_no(workflow_id, task.name),
                input_hash=input_hash,
                status="running",
                started_at=datetime.now(timezone.utc),
            )
            self.repository.save_node_attempt(attempt)

            try:
                output = self._run_executor(task, inputs, run)
            except Exception as exc:
                self.budget.release(workflow_id, f"dynamic:{task.name}")
                error_code = classify_error(exc)
                self.repository.save_node_attempt(
                    replace(
                        attempt,
                        status="failed",
                        error_code=error_code,
                        ended_at=datetime.now(timezone.utc),
                    )
                )
                failed_attempts += 1
                if should_retry(error_code, failed_attempts):
                    self._sleep(compute_backoff_seconds(failed_attempts - 1, jitter=False))
                    continue
                return self._task_failed(task, error_code)

            elapsed = elapsed_since(started)
            settle_amounts = dict(amounts)
            settle_amounts["elapsed_seconds"] = max(1, elapsed)
            self.budget.settle(workflow_id, f"dynamic:{task.name}", settle_amounts)
            self.repository.save_node_attempt(
                replace(
                    attempt,
                    status="succeeded",
                    output=output,
                    ended_at=datetime.now(timezone.utc),
                )
            )

            self._update_blackboard(
                workflow_id,
                {f"task_outputs.{task.output_field or task.name}": output},
            )
            return self._task_succeeded(task, output)

    def _run_executor(
        self,
        task: ResearchTask,
        inputs: dict[str, Any],
        run: WorkflowRun,
    ) -> dict[str, Any]:
        """根据 agent_key 分发到具体执行器。"""
        executor = self._executors.get(task.agent_key)
        if executor is None:
            raise TaskExecutionError(f"NO_EXECUTOR_FOR_AGENT:{task.agent_key}")
        return executor(task, inputs, run)

    # --- built-in executors (MVP deterministic fallbacks) ---

    def _execute_retriever(
        self, task: ResearchTask, inputs: dict[str, Any], run: WorkflowRun
    ) -> dict[str, Any]:
        question = inputs.get("question", "")
        as_of = run.as_of
        try:
            trace = self.retrieval_service.retrieve(
                RetrievalRequest(
                    query=question,
                    retrieval_mode="planned",
                    top_k=10,
                    as_of=as_of,
                )
            )
            return {
                "candidate_count": trace.candidate_count,
                "items": [
                    {
                        "chunk_id": item.chunk_id,
                        "document_id": item.document_id,
                        "text": item.text,
                        "score": item.score,
                        "backend": item.backend,
                    }
                    for item in trace.items
                ],
                "backend_coverage": trace.backend_coverage,
                "model_run_id": None,
            }
        except Exception:
            # 检索失败不阻塞后续任务；返回空结果并标记降级
            return {
                "candidate_count": 0,
                "items": [],
                "backend_coverage": {},
                "model_run_id": None,
                "degraded": True,
            }

    def _execute_fact_checker(
        self, task: ResearchTask, inputs: dict[str, Any], run: WorkflowRun
    ) -> dict[str, Any]:
        agent = FactCheckerAgent(self.model_gateway or object())  # type: ignore[arg-type]
        # 动态模式下，fact_checker 只需要一个包含 claim_ids 的占位状态
        state = {
            "event_id": run.event_id,
            "workflow_id": run.id,
            "as_of": run.as_of.isoformat(),
        }
        if run.event_id:
            return agent.run(state, self.repository, object())  # type: ignore[arg-type]
        return {
            "event_id": run.event_id,
            "claim_ids": [],
            "verified_claim_ids": [],
            "model_run_id": None,
            "analysis_ref": "fact_check_snapshot",
        }

    def _execute_company_analyst(
        self, task: ResearchTask, inputs: dict[str, Any], run: WorkflowRun
    ) -> dict[str, Any]:
        if self.model_gateway is None:
            return self._fallback_company_analysis(inputs, run)
        agent = CompanyAnalystAgent(self.model_gateway, self._tool_gateway())
        state = {
            "event_id": run.event_id,
            "workflow_id": run.id,
            "as_of": run.as_of.isoformat(),
            "fact_check_snapshot": inputs.get("fact_verify", {}),
        }
        if run.event_id:
            return agent.run(state, self.repository)
        return self._fallback_company_analysis(inputs, run)

    def _execute_skeptic(
        self, task: ResearchTask, inputs: dict[str, Any], run: WorkflowRun
    ) -> dict[str, Any]:
        if self.model_gateway is None:
            return self._fallback_skeptic(inputs, run)
        agent = SkepticAgent(self.model_gateway)
        state = {
            "event_id": run.event_id,
            "workflow_id": run.id,
            "as_of": run.as_of.isoformat(),
            "company_analysis": inputs.get("company_analyze", {}),
        }
        if inputs.get("company_analyze"):
            return agent.run(state, self.repository)
        return self._fallback_skeptic(inputs, run)

    def _execute_synthesizer(
        self, task: ResearchTask, inputs: dict[str, Any], run: WorkflowRun
    ) -> dict[str, Any]:
        if self.model_gateway is None:
            return self._fallback_synthesis(inputs, run)
        agent = SynthesizerAgent(self.model_gateway)
        state = {
            "event_id": run.event_id,
            "workflow_id": run.id,
            "as_of": run.as_of.isoformat(),
            "fact_check_snapshot": inputs.get("fact_verify", {}),
            "company_analysis": inputs.get("company_analyze", {}),
            "counter_analysis": inputs.get("skeptic_review", {}),
        }
        return agent.run(state, self.repository)

    def _execute_impact_analyst(
        self, task: ResearchTask, inputs: dict[str, Any], run: WorkflowRun
    ) -> dict[str, Any]:
        if not run.event_id:
            return self._fallback_impact(inputs, run)
        service = ImpactAnalysisService(self.repository)
        analysis = service.generate(run.event_id, actor="dynamic_workflow")
        return {
            "id": analysis.id,
            "event_id": analysis.event_id,
            "summary": analysis.summary,
            "transmission_chains": analysis.transmission_chains,
            "impacts": analysis.impacts,
            "macro_assumptions": analysis.macro_assumptions,
            "watch_items": analysis.watch_items,
            "model_run_id": analysis.model_run_id,
            "degraded": analysis.degraded,
        }

    def _execute_market_analyst(
        self, task: ResearchTask, inputs: dict[str, Any], run: WorkflowRun
    ) -> dict[str, Any]:
        return self._fallback_generic_analysis(
            inputs, run, analysis_ref="market_analysis", focus="市场情绪与流动性"
        )

    def _execute_industry_analyst(
        self, task: ResearchTask, inputs: dict[str, Any], run: WorkflowRun
    ) -> dict[str, Any]:
        return self._fallback_generic_analysis(
            inputs, run, analysis_ref="industry_analysis", focus="产业链与行业传导"
        )

    def _execute_regulatory_analyst(
        self, task: ResearchTask, inputs: dict[str, Any], run: WorkflowRun
    ) -> dict[str, Any]:
        return self._fallback_generic_analysis(
            inputs, run, analysis_ref="regulatory_analysis", focus="政策与监管影响"
        )

    # --- fallback outputs for tests / no-LLM paths ---

    def _fallback_company_analysis(
        self, inputs: dict[str, Any], run: WorkflowRun
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "analysis_ref": "company_analysis",
            "status": "partial",
            "direction": "uncertain",
            "impact_horizon": "short_term",
            "assumptions": [],
            "financial_impacts": [],
            "scenarios": [
                {
                    "name": "base",
                    "assumption_ids": [],
                    "outcome": "待补充",
                    "probability_label": "not_assessed",
                }
            ],
            "risks": [],
            "missing_data": ["dynamic_workflow_no_llm"],
            "confidence": 0.4,
            "confidence_factors": [],
        }

    def _fallback_skeptic(self, inputs: dict[str, Any], run: WorkflowRun) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "analysis_ref": "counter_analysis",
            "status": "insufficient_evidence",
            "counter_arguments": [],
            "fragile_assumptions": [],
            "thesis_breakers": [],
            "direction_assessment": "inconclusive",
            "recommended_confidence": 0.4,
            "confidence_reasons": ["dynamic_workflow_no_llm"],
            "review_required": False,
        }

    def _fallback_synthesis(self, inputs: dict[str, Any], run: WorkflowRun) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "analysis_ref": "synthesis",
            "status": "complete",
            "signal": "uncertain",
            "confidence": 0.4,
            "horizon": "uncertain",
            "summary": "动态研究完成（无 LLM 降级输出）。",
            "key_fact_claim_ids": [],
            "supporting_points": [],
            "counter_points": [],
            "watch_items": [],
            "reanalysis_triggers": [],
            "limitations": ["dynamic_workflow_no_llm"],
            "confidence_factors": [],
        }

    def _fallback_impact(self, inputs: dict[str, Any], run: WorkflowRun) -> dict[str, Any]:
        return {
            "summary": "无关联事件，无法生成影响分析。",
            "transmission_chains": [],
            "impacts": [],
            "macro_assumptions": [],
            "watch_items": [],
            "degraded": True,
        }

    def _fallback_generic_analysis(
        self,
        inputs: dict[str, Any],
        run: WorkflowRun,
        *,
        analysis_ref: str,
        focus: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "analysis_ref": analysis_ref,
            "status": "partial",
            "focus": focus,
            "direction": "uncertain",
            "key_findings": [],
            "assumptions": [],
            "risks": [],
            "missing_data": ["dynamic_workflow_no_llm"],
            "confidence": 0.4,
            "confidence_factors": [],
        }

    def _tool_gateway(self):
        from app.research.tools.gateway import ToolGateway

        return ToolGateway(self.repository)

    # --- helpers ---

    def _collect_inputs(self, task: ResearchTask, blackboard: dict[str, Any]) -> dict[str, Any]:
        outputs = blackboard.get("task_outputs", {})
        inputs: dict[str, Any] = {"question": blackboard.get("research_plan", {}).get("question")}
        for field in task.input_fields:
            if field in outputs:
                inputs[field] = outputs[field]
        return inputs

    def _update_blackboard(self, workflow_id: str, updates: dict[str, Any]) -> None:
        run = self.repository.get_workflow_run(workflow_id)
        blackboard = dict(run.blackboard or {})
        for key, value in updates.items():
            if "." in key:
                parent, child = key.split(".", 1)
                blackboard.setdefault(parent, {})[child] = value
            else:
                blackboard[key] = value
        self.repository.update_workflow_run(replace(run, blackboard=blackboard))

    def _blackboard(self, workflow_id: str) -> dict[str, Any]:
        run = self.repository.get_workflow_run(workflow_id)
        return dict(run.blackboard or {})

    def _state_version(self, workflow_id: str) -> int:
        run = self.repository.get_workflow_run(workflow_id)
        return run.state_version + 1 if run else 0

    def _next_attempt_no(self, workflow_id: str, task_name: str) -> int:
        attempts = self.repository.list_node_attempts(workflow_id, f"dynamic:{task_name}")
        if not attempts:
            return 1
        return max(a.attempt_no for a in attempts) + 1

    def _task_succeeded(self, task: ResearchTask, output: dict[str, Any]) -> ResearchTask:
        now = datetime.now(timezone.utc)
        return replace(
            task,
            status="succeeded",
            input_hash=_sha256(output),
            output_snapshot=output,
            ended_at=now,
            started_at=task.started_at or now,
        )

    def _task_failed(self, task: ResearchTask, error_code: str) -> ResearchTask:
        return replace(
            task,
            status="failed",
            review_reason=error_code,
            ended_at=datetime.now(timezone.utc),
        )

    def _handle_budget_exceeded(
        self, plan: ResearchPlan, run: WorkflowRun, exc: BudgetExceeded
    ) -> ResearchPlan:
        plan = replace(
            plan,
            status="waiting_review",
            updated_at=datetime.now(timezone.utc),
        )
        self.repository.update_research_plan(plan)
        self.repository.update_workflow_run(replace(run, status="waiting_review"))
        self.repository.save_review_task(
            ReviewTask(
                id=new_id("rvt"),
                object_type="workflow",
                object_id=run.id,
                reason_code="BUDGET_HARD_LIMIT",
                allowed_decisions=["approve", "return", "downgrade_to_fact_card", "reject"],
                resume_from=None,
                blackboard_version=self._state_version(run.id),
                created_at=datetime.now(timezone.utc),
            )
        )
        return plan

    def _handle_failure(self, plan: ResearchPlan, run: WorkflowRun, reason: str) -> ResearchPlan:
        plan = replace(
            plan,
            status="failed",
            updated_at=datetime.now(timezone.utc),
        )
        self.repository.update_research_plan(plan)
        self.repository.update_workflow_run(replace(run, status="failed", error_code=reason))
        return plan

    @staticmethod
    def _plan_to_blackboard(plan: ResearchPlan) -> dict[str, Any]:
        return {
            "id": plan.id,
            "question": plan.question,
            "objective": plan.objective,
            "as_of": plan.as_of.isoformat(),
            "status": plan.status,
            "budget_profile": plan.budget_profile,
            "completion_criteria": plan.completion_criteria,
            "metadata": plan.metadata,
        }
