import hashlib
import json
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from app.domain import NodeAttempt, ReviewTask, WorkflowRun
from app.model_gateway.service import ModelGateway
from app.platform.asof import ensure_within_as_of
from app.platform.ids import new_id
from app.publishing.assembler import ReportAssembler
from app.publishing.guardrail import GuardrailEngine
from app.publishing.service import FactCardService
from app.research.tools.gateway import ToolGateway
from app.review.service import AutoReviewService
from app.workflows.agents import (
    CompanyAnalystAgent,
    FactCheckerAgent,
    SkepticAgent,
    SynthesizerAgent,
)
from app.workflows.blackboard import BlackboardGuard
from app.workflows.budget import BudgetExceeded, BudgetManager, elapsed_since
from app.workflows.checkpoints import CheckpointerFactory, checkpointer_factory_for
from app.workflows.errors import (
    classify_error,
    compute_backoff_seconds,
    default_sleep,
    should_retry,
)
from app.workflows.invalidation import apply_invalidation, nodes_to_invalidate

logger = logging.getLogger(__name__)


class ResearchState(TypedDict, total=False):
    event_id: str
    workflow_id: str
    as_of: str
    event_snapshot: dict
    fact_check_snapshot: dict
    company_analysis: dict
    counter_analysis: dict
    synthesis: dict
    guardrail_result: dict
    report_draft_ref: str
    report_draft: dict
    degraded_mode: str
    degradation_reason: str
    provenance: dict


WORKFLOW_REVIEW_DECISIONS = (
    "approve",
    "return",
    "return_for_supplement",
    "downgrade_to_fact_card",
    "reject",
)


class WorkflowService:
    def __init__(
        self,
        repository,
        model_gateway: ModelGateway | None = None,
        budget_manager: BudgetManager | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        checkpointer_factory: CheckpointerFactory | None = None,
    ) -> None:
        self.repository = repository
        self.model_gateway = model_gateway or ModelGateway(repository)
        self.tool_gateway = ToolGateway(repository)
        self.budget = budget_manager or BudgetManager(repository)
        self.blackboard_guard = BlackboardGuard(repository)
        self.fact_checker = FactCheckerAgent(self.model_gateway)
        self.company_analyst = CompanyAnalystAgent(self.model_gateway, self.tool_gateway)
        self.skeptic = SkepticAgent(self.model_gateway)
        self.synthesizer = SynthesizerAgent(self.model_gateway)
        self.assembler = ReportAssembler(repository)
        self.guardrail_engine = GuardrailEngine()
        self._sleep = sleep_fn or default_sleep
        self.checkpointer_factory = checkpointer_factory or checkpointer_factory_for(repository)
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(ResearchState)
        graph.add_node("context", self._context)
        graph.add_node("fact_check", self._fact_check)
        graph.add_node("company", self._company)
        graph.add_node("skeptic", self._skeptic)
        graph.add_node("synthesize", self._synthesize)
        graph.add_node("guardrail", self._guardrail)
        graph.add_node("draft", self._draft)
        graph.add_edge(START, "context")
        graph.add_edge("context", "fact_check")
        graph.add_edge("fact_check", "company")
        graph.add_edge("company", "skeptic")
        graph.add_edge("skeptic", "synthesize")
        graph.add_edge("synthesize", "draft")
        graph.add_edge("draft", "guardrail")
        graph.add_edge("guardrail", END)
        return graph.compile(checkpointer=self.checkpointer_factory.create())

    def create(self, event_id: str, trigger_id: str, as_of: datetime | None = None) -> WorkflowRun:
        run = WorkflowRun(
            new_id("wfr"),
            event_id,
            trigger_id,
            "pending",
            as_of or datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        self._save_new_workflow_run(run)
        return run

    def _save_new_workflow_run(self, run: WorkflowRun) -> None:
        """Persist a new run."""
        self.repository.save_workflow_run(run)

    def run(self, workflow_id: str) -> WorkflowRun:
        run = self.repository.get_workflow_run(workflow_id)
        if not run:
            raise KeyError("WORKFLOW_NOT_FOUND")
        self.repository.update_workflow_run(replace(run, status="running"))
        thread_id = self._thread_id(run)
        try:
            state = self._invoke(run, thread_id, recover=True)
        except BudgetExceeded as exc:
            return self._handle_budget_exceeded(run, exc, thread_id)
        except Exception as exc:
            error_code = classify_error(exc)
            if error_code == "BUDGET_HARD_LIMIT":
                return self._handle_budget_exceeded(run, exc, thread_id)
            result = replace(run, status="failed", error_code=error_code)
            self.repository.update_workflow_run(result)
            return result
        else:
            return self._finalize_success(run, state)

    def resume(
        self,
        workflow_id: str,
        *,
        trigger: str = "budget_resume",
        resume_from: str | None = None,
        budget_adjust: dict[str, int] | None = None,
        actor_id: str | None = None,
        reason: str = "review_resume",
        force_fact_only: bool = False,
    ) -> WorkflowRun:
        """从 waiting_review / failed 恢复；按失效表清字段并可选提升预算。"""
        run = self.repository.get_workflow_run(workflow_id)
        if not run:
            raise KeyError("WORKFLOW_NOT_FOUND")
        if run.status not in {"waiting_review", "failed"}:
            raise ValueError("WORKFLOW_NOT_RESUMABLE")

        nodes = nodes_to_invalidate(trigger, resume_from)
        blackboard = apply_invalidation(dict(run.blackboard or {}), nodes)
        if nodes:
            self.repository.invalidate_node_attempts(run.id, nodes)

        if force_fact_only or trigger == "downgrade_fact_only":
            return self._complete_as_fact_only(
                replace(run, blackboard=blackboard),
                blackboard,
                reason="review_downgrade_to_fact_card",
            )

        if budget_adjust:
            self.budget.adjust(
                run.id, budget_adjust, reason=reason, actor_id=actor_id
            )

        resume_count = int(blackboard.get("_resume_count", 0)) + 1
        blackboard["_resume_count"] = resume_count
        blackboard.pop("degradation_reason", None)
        updated = replace(
            run,
            status="running",
            blackboard=blackboard,
            state_version=run.state_version + 1,
            error_code=None,
        )
        self.repository.update_workflow_run(updated)
        thread_id = f"{run.id}:r{resume_count}"
        try:
            state = self._invoke(updated, thread_id)
        except BudgetExceeded as exc:
            return self._handle_budget_exceeded(updated, exc, thread_id)
        except Exception as exc:
            error_code = classify_error(exc)
            result = replace(updated, status="failed", error_code=error_code)
            self.repository.update_workflow_run(result)
            return result
        else:
            return self._finalize_success(updated, state)

    def cancel(self, workflow_id: str, *, reason: str) -> WorkflowRun:
        run = self.repository.get_workflow_run(workflow_id)
        if not run:
            raise KeyError("WORKFLOW_NOT_FOUND")
        if run.status not in {"waiting_review", "failed", "running", "pending"}:
            raise ValueError("WORKFLOW_NOT_CANCELLABLE")
        blackboard = dict(run.blackboard or {})
        blackboard["cancel_reason"] = reason
        result = replace(run, status="cancelled", blackboard=blackboard)
        self.repository.update_workflow_run(result)
        return result

    def _initial_state(self, run: WorkflowRun) -> dict[str, Any]:
        state: dict[str, Any] = {
            "event_id": run.event_id,
            "workflow_id": run.id,
            "as_of": run.as_of.isoformat(),
        }
        for key, value in (run.blackboard or {}).items():
            if key.startswith("_"):
                continue
            if value is not None:
                state[key] = value
        return state

    @staticmethod
    def _thread_id(run: WorkflowRun) -> str:
        resume_count = int((run.blackboard or {}).get("_resume_count", 0))
        if resume_count:
            return f"{run.id}:r{resume_count}"
        return run.id

    def _checkpoint_values(self, thread_id: str) -> dict[str, Any]:
        try:
            snapshot = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        except Exception as exc:
            logger.warning(
                "checkpoint read failed: thread_id=%s type=%s error=%s",
                thread_id,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return {}
        if snapshot is None:
            return {}
        values = getattr(snapshot, "values", None) or {}
        return dict(values)

    def _invoke(
        self, run: WorkflowRun, thread_id: str, *, recover: bool = False
    ) -> dict[str, Any]:
        config = {"configurable": {"thread_id": thread_id}}
        graph_input: dict[str, Any] | None = self._initial_state(run)
        if recover:
            try:
                snapshot = self.graph.get_state(config)
            except Exception as exc:
                logger.warning(
                    "checkpoint recover failed: thread_id=%s type=%s error=%s",
                    thread_id,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                snapshot = None
            if snapshot is not None and getattr(snapshot, "next", ()):
                # None resumes the pending task from its durable checkpoint. Supplying
                # fresh input here would start another graph turn from START.
                graph_input = None
        return self.graph.invoke(graph_input, config)

    def _handle_budget_exceeded(
        self, run: WorkflowRun, exc: BaseException, thread_id: str
    ) -> WorkflowRun:
        blackboard = dict(run.blackboard or {})
        blackboard.update(self._checkpoint_values(thread_id))
        claims = self.repository.get_claims_for_event(run.event_id, as_of=run.as_of)
        verified = [claim for claim in claims if claim.status == "verified"]
        if verified:
            return self._complete_as_fact_only(
                run, blackboard, reason=str(exc) or "BUDGET_HARD_LIMIT"
            )
        return self._enter_waiting_review(
            run,
            blackboard,
            reason_code="BUDGET_HARD_LIMIT",
            error_code=str(exc) or "BUDGET_HARD_LIMIT",
            resume_from="company",
        )

    def _complete_as_fact_only(
        self, run: WorkflowRun, blackboard: dict[str, Any], *, reason: str
    ) -> WorkflowRun:
        event = self.repository.get_event(run.event_id)
        if event is None:
            result = replace(run, status="failed", error_code="EVENT_NOT_FOUND")
            self.repository.update_workflow_run(result)
            return result
        claims = self.repository.get_claims_for_event(run.event_id, as_of=run.as_of)
        verified_ids = [c.id for c in claims if c.status == "verified"]
        synthesis = {
            "status": "fact_only",
            "confidence": 0.5,
            "summary": "分析受限，已降级为事实卡片。",
            "schema_version": "1.0.0",
            "key_fact_claim_ids": verified_ids,
        }
        blackboard = dict(blackboard)
        blackboard["synthesis"] = synthesis
        blackboard["degraded_mode"] = "fact_only"
        blackboard["degradation_reason"] = reason
        blackboard["provenance"] = self._collect_provenance(blackboard)
        draft_run = replace(run, blackboard=blackboard, status="running")
        draft = self.assembler.assemble(draft_run, event)
        blackboard["report_draft"] = draft
        blackboard["report_draft_ref"] = f"workflow:{run.event_id}"
        blackboard["guardrail_result"] = {
            "passed": True,
            "review_required": False,
            "checked": True,
            "degraded": True,
            "version": "guardrail-v1",
            "rules": [],
        }
        FactCardService(self.repository).create_from_draft(
            event, run, draft, status="published"
        )
        result = replace(
            run,
            status="succeeded",
            blackboard=blackboard,
            state_version=self._state_version(blackboard),
            error_code=None,
        )
        self.repository.update_workflow_run(result)
        return result

    def _finalize_success(self, run: WorkflowRun, state: dict[str, Any]) -> WorkflowRun:
        """Guardrail 通过后持久化报告；未通过则进入 waiting_review 或失败。"""
        state["provenance"] = self._collect_provenance(state)
        draft = state.get("report_draft") or {}
        guardrail_result = state.get("guardrail_result") or {}
        # 自定义/降级流程可能没有 report_draft，此时跳过 Guardrail 校验。
        if draft and not guardrail_result.get("checked"):
            result = replace(
                run,
                status="failed",
                state_version=self._state_version(state),
                blackboard=dict(state),
                error_code="GUARDRAIL_NOT_CHECKED",
            )
            self.repository.update_workflow_run(result)
            return result
        if draft and not guardrail_result.get("passed"):
            if guardrail_result.get("review_required"):
                blackboard = dict(state)
                blackboard["degradation_reason"] = "GUARDRAIL_REVIEW_REQUIRED"
                return self._enter_waiting_review(
                    run,
                    blackboard,
                    reason_code="GUARDRAIL_REVIEW_REQUIRED",
                    error_code=None,
                    resume_from="draft",
                )
            blocking = [
                rule
                for rule in guardrail_result.get("rules", [])
                if rule.get("status") == "fail"
            ]
            error_code = (
                f"GUARDRAIL_{blocking[0]['rule'].upper()}"
                if blocking
                else "GUARDRAIL_BLOCKED"
            )
            result = replace(
                run,
                status="failed",
                state_version=self._state_version(state),
                blackboard=dict(state),
                error_code=error_code,
            )
            self.repository.update_workflow_run(result)
            return result

        event = self.repository.get_event(run.event_id)
        if event and draft:
            card = FactCardService(self.repository).create_from_draft(
                event, run, draft, status="needs_review"
            )
            self.repository.save_review_task(
                ReviewTask(
                    id=new_id("rvt"),
                    object_type="report",
                    object_id=card.id,
                    reason_code="REPORT_REVIEW_REQUIRED",
                    allowed_decisions=["approve", "return", "reject"],
                    created_at=datetime.now(timezone.utc),
                )
            )
        result = replace(
            run,
            status="succeeded",
            state_version=self._state_version(state),
            blackboard=dict(state),
            error_code=None,
        )
        self.repository.update_workflow_run(result)
        return result

    def _enter_waiting_review(
        self,
        run: WorkflowRun,
        blackboard: dict[str, Any],
        *,
        reason_code: str,
        error_code: str | None,
        resume_from: str | None,
    ) -> WorkflowRun:
        blackboard = dict(blackboard)
        if error_code:
            blackboard["degradation_reason"] = error_code
        state_version = self._state_version(blackboard)
        result = replace(
            run,
            status="waiting_review",
            blackboard=blackboard,
            state_version=state_version,
            error_code=error_code or None,
        )
        self.repository.update_workflow_run(result)
        task = ReviewTask(
            id=new_id("rvt"),
            object_type="workflow",
            object_id=run.id,
            reason_code=reason_code,
            allowed_decisions=list(WORKFLOW_REVIEW_DECISIONS),
            resume_from=resume_from,
            blackboard_version=state_version,
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_review_task(task)
        AutoReviewService(self.repository).attempt_workflow_review(task)
        return result

    @staticmethod
    def _state_version(state: dict[str, Any]) -> int:
        """Blackboard 版本 = 已写入的所有权字段数。"""
        owned = {
            "event_snapshot",
            "fact_check_snapshot",
            "company_analysis",
            "counter_analysis",
            "synthesis",
            "guardrail_result",
            "report_draft_ref",
        }
        return sum(1 for key in owned if key in state and state[key])

    @staticmethod
    def _collect_provenance(state: dict[str, Any]) -> dict[str, list[str]]:
        """Collect only IDs and references emitted by completed Agent outputs."""
        model_run_ids: list[str] = []
        analysis_refs: list[str] = []
        for key in (
            "fact_check_snapshot",
            "company_analysis",
            "counter_analysis",
            "synthesis",
        ):
            block = state.get(key)
            if not isinstance(block, dict):
                continue
            model_run_id = block.get("model_run_id")
            analysis_ref = block.get("analysis_ref")
            if isinstance(model_run_id, str) and model_run_id and model_run_id not in model_run_ids:
                model_run_ids.append(model_run_id)
            if isinstance(analysis_ref, str) and analysis_ref and analysis_ref not in analysis_refs:
                analysis_refs.append(analysis_ref)
        return {
            "model_run_ids": model_run_ids,
            "analysis_refs": analysis_refs,
        }

    def _execute_node(
        self,
        node_name: str,
        node_fn: Callable[[ResearchState], dict[str, Any]],
        state: ResearchState,
        input_fields: tuple[str, ...],
        reserve_amounts: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """节点执行包装：幂等命中复用 + 预算预留 + 重试退避 + 结算 + 写 NodeAttempt。"""
        workflow_id = state.get("workflow_id", "")
        input_hash = self._input_hash(state, input_fields)
        amounts = reserve_amounts or {"model_calls": 1, "tool_calls": 2, "elapsed_seconds": 1}

        existing = self.repository.find_node_attempt(workflow_id, node_name, input_hash)
        if existing is not None and existing.output is not None:
            return existing.output

        failed_attempts = 0

        while True:
            self.budget.reserve(workflow_id, node_name, amounts)
            started = time.perf_counter()
            attempt = NodeAttempt(
                id=new_id("nat"),
                workflow_id=workflow_id,
                node_name=node_name,
                attempt_no=self._next_attempt_no(workflow_id, node_name),
                input_hash=input_hash,
                status="running",
                started_at=datetime.now(timezone.utc),
            )
            self.repository.save_node_attempt(attempt)

            try:
                output = node_fn(state)
            except Exception as exc:
                self.budget.release(workflow_id, node_name)
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
                raise

            elapsed = elapsed_since(started)
            settle_amounts = dict(amounts)
            settle_amounts["elapsed_seconds"] = max(1, elapsed)
            self.budget.settle(workflow_id, node_name, settle_amounts)
            self.repository.save_node_attempt(
                replace(
                    attempt,
                    status="succeeded",
                    output=output,
                    ended_at=datetime.now(timezone.utc),
                )
            )
            return output

    def _next_attempt_no(self, workflow_id: str, node_name: str) -> int:
        attempts = self.repository.list_node_attempts(workflow_id, node_name)
        if not attempts:
            return 1
        return max(attempt.attempt_no for attempt in attempts) + 1

    @staticmethod
    def _input_hash(state: ResearchState, input_fields: tuple[str, ...]) -> str:
        """由节点实际读取的 Blackboard 字段生成 input_hash。"""
        payload = {key: state.get(key) for key in input_fields}
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    # --- 节点实现 ---

    def _context(self, state: ResearchState) -> dict[str, Any]:
        def _run(s: ResearchState) -> dict[str, Any]:
            event = self.repository.get_event(s["event_id"])
            ensure_within_as_of(event, datetime.fromisoformat(s["as_of"]), context="get_event")
            return {
                "event_snapshot": {
                    "id": event.id,
                    "title": event.title,
                    "event_type": event.event_type,
                    "key_fields": event.key_fields,
                }
            }

        return self._execute_node(
            "context",
            _run,
            state,
            input_fields=(),
            reserve_amounts={"model_calls": 0, "tool_calls": 0, "elapsed_seconds": 1},
        )

    def _fact_check(self, state: ResearchState) -> dict[str, Any]:
        return self._execute_node(
            "fact_check",
            lambda s: self.fact_checker.run(s, self.repository, self.tool_gateway),
            state,
            input_fields=("event_snapshot",),
            reserve_amounts={"model_calls": 1, "tool_calls": 2, "elapsed_seconds": 1},
        )

    def _company(self, state: ResearchState) -> dict[str, Any]:
        return self._execute_node(
            "company",
            lambda s: self.company_analyst.run(s, self.repository),
            state,
            input_fields=("event_snapshot", "fact_check_snapshot"),
            reserve_amounts={"model_calls": 1, "tool_calls": 2, "elapsed_seconds": 1},
        )

    def _skeptic(self, state: ResearchState) -> dict[str, Any]:
        return self._execute_node(
            "skeptic",
            lambda s: self.skeptic.run(s, self.repository),
            state,
            input_fields=("company_analysis", "fact_check_snapshot"),
            reserve_amounts={"model_calls": 1, "tool_calls": 1, "elapsed_seconds": 1},
        )

    def _synthesize(self, state: ResearchState) -> dict[str, Any]:
        return self._execute_node(
            "synthesize",
            lambda s: self.synthesizer.run(s, self.repository),
            state,
            input_fields=("fact_check_snapshot", "company_analysis", "counter_analysis"),
            reserve_amounts={"model_calls": 1, "tool_calls": 0, "elapsed_seconds": 1},
        )

    def _draft(self, state: ResearchState) -> dict[str, Any]:
        def _run(s: ResearchState) -> dict[str, Any]:
            run = self.repository.get_workflow_run(s["workflow_id"])
            event = self.repository.get_event(s["event_id"])
            if run is None or event is None:
                return {"report_draft_ref": None, "report_draft": None}
            # 装配时合并当前图状态，确保降级 synthesis 生效
            blackboard = {**(run.blackboard or {}), **dict(s)}
            blackboard["provenance"] = self._collect_provenance(blackboard)
            merged = replace(run, blackboard=blackboard)
            draft = self.assembler.assemble(merged, event)
            return {"report_draft_ref": f"workflow:{s['event_id']}", "report_draft": draft}

        return self._execute_node(
            "draft",
            _run,
            state,
            input_fields=(
                "synthesis",
                "company_analysis",
                "counter_analysis",
                "fact_check_snapshot",
            ),
            reserve_amounts={"model_calls": 0, "tool_calls": 0, "elapsed_seconds": 1},
        )

    def _guardrail(self, state: ResearchState) -> dict[str, Any]:
        def _run(s: ResearchState) -> dict[str, Any]:
            draft = s.get("report_draft") or {}
            run = self.repository.get_workflow_run(s["workflow_id"])
            if not draft or run is None:
                return {"guardrail_result": {"passed": False, "checked": False, "rules": []}}
            claims = self.repository.get_claims_for_event(s["event_id"], as_of=run.as_of)
            result = self.guardrail_engine.evaluate(draft, claims, run.as_of)
            return {
                "guardrail_result": {
                    "passed": result.passed,
                    "review_required": result.review_required,
                    "checked": True,
                    "version": result.version,
                    "rules": [
                        {
                            "rule": r.rule,
                            "status": r.status,
                            "message": r.message,
                            "fix_suggestion": r.fix_suggestion,
                        }
                        for r in result.rules
                    ],
                }
            }

        return self._execute_node(
            "guardrail",
            _run,
            state,
            input_fields=("report_draft",),
            reserve_amounts={"model_calls": 0, "tool_calls": 0, "elapsed_seconds": 1},
        )
