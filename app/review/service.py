from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from app.domain import (
    DEFAULT_REVIEWER_ID,
    AuditLog,
    AutoReviewAttempt,
    Claim,
    ConflictRecord,
    FactCard,
    MergeReviewTask,
    ReviewTask,
)
from app.model_gateway.service import ModelGateway
from app.platform.ids import new_id
from app.platform.settings import Settings
from app.review.agents import DefaultReviewerAgent
from app.review.schemas import AutoReviewDecision


class AutoReviewService:
    """自动审核服务：规则优先，LLM 兜底，拿不准的留给人工。"""

    def __init__(
        self,
        repository,
        settings: Optional[Settings] = None,
        agent: Optional[DefaultReviewerAgent] = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or Settings.from_environment()
        self.agent = agent or DefaultReviewerAgent(ModelGateway(repository))

    def mode(self) -> str:
        if self.settings.auto_review_disabled:
            return "human"
        policy_getter = getattr(self.repository, "get_review_policy", None)
        policy = policy_getter() if callable(policy_getter) else None
        return getattr(policy, "mode", None) or self.settings.review_mode

    def attempt_task(self, task: ReviewTask) -> Optional[AutoReviewDecision]:
        """Process a queued task through the configured Agent policy."""
        if self._has_attempt(task.id):
            return None
        if self.mode() != "agent":
            self._record_attempt(task, "disabled", None, "审核方式为人工")
            return None
        decision: Optional[AutoReviewDecision]
        if task.object_type == "report":
            card = self.repository.get_fact_card(task.object_id)
            if card:
                return self.attempt_report_review(task, card)
            decision = None
        elif task.object_type == "claim_conflict":
            conflict = self.repository.get_conflict(task.object_id)
            if conflict:
                return self.attempt_conflict_review(task, conflict)
            decision = None
        elif task.object_type == "workflow":
            return self.attempt_workflow_review(task)
        else:
            decision = None
        if decision is None:
            self._record_attempt(task, "escalated", None, "Agent 未能在质量门内作出决定")
        return decision

    def attempt_report_review(
        self, task: ReviewTask, card: FactCard
    ) -> Optional[AutoReviewDecision]:
        """尝试自动审核 report 类型任务。"""
        if self.mode() != "agent" or "report" not in self.settings.auto_review_enabled_types:
            return None
        decision = self._decide_report(task, card)
        if decision is None or decision.escalate:
            if decision is not None:
                self._record_attempt(task, "escalated", decision, decision.reason)
            return None
        if decision.confidence < self._min_confidence():
            self._record_attempt(task, "escalated", decision, "置信度低于自动审核门槛")
            return None
        self._apply_report_decision(task, card, decision)
        self._record_attempt(task, "decided", decision, decision.reason)
        return decision

    def attempt_conflict_review(
        self, task: ReviewTask, conflict: ConflictRecord
    ) -> Optional[AutoReviewDecision]:
        """尝试自动审核 claim_conflict 类型任务。"""
        if (
            self.mode() != "agent"
            or "claim_conflict" not in self.settings.auto_review_enabled_types
        ):
            return None
        decision = self._decide_conflict(task, conflict)
        if decision is None or decision.escalate:
            if decision is not None:
                self._record_attempt(task, "escalated", decision, decision.reason)
            return None
        if decision.confidence < self._min_confidence():
            self._record_attempt(task, "escalated", decision, "置信度低于自动审核门槛")
            return None
        self._apply_conflict_decision(task, conflict, decision)
        self._record_attempt(task, "decided", decision, decision.reason)
        return decision

    def attempt_workflow_review(self, task: ReviewTask) -> Optional[AutoReviewDecision]:
        """默认由 Agent 审核工作流质量门；无法安全决定时再升级人工。"""
        if self.mode() != "agent" or "workflow" not in self.settings.auto_review_enabled_types:
            self._record_attempt(task, "disabled", None, "工作流自动审核未启用")
            return None
        run = self.repository.get_workflow_run(task.object_id)
        context = {
            "task_type": "workflow",
            "reason_code": task.reason_code,
            "workflow_id": task.object_id,
            "blackboard": (run.blackboard if run else {}),
            "allowed_decisions": task.allowed_decisions,
        }
        decision = self._maybe_llm(context)
        if decision is None or decision.escalate:
            self._record_attempt(
                task, "escalated", decision, decision.reason if decision else "Agent 未能作出决定"
            )
            return None
        if decision.confidence < self._min_confidence():
            self._record_attempt(task, "escalated", decision, "置信度低于自动审核门槛")
            return None
        self._mark_task_decided(task, decision)
        if run and decision.decision in {
            "approve",
            "return",
            "return_for_supplement",
            "downgrade_to_fact_card",
        }:
            from app.workflows.service import WorkflowService

            if decision.decision == "downgrade_to_fact_card":
                WorkflowService(self.repository).resume(
                    run.id,
                    trigger="downgrade_fact_only",
                    force_fact_only=True,
                    actor_id=DEFAULT_REVIEWER_ID,
                    reason=decision.reason,
                )
            else:
                WorkflowService(self.repository).resume(
                    run.id,
                    trigger="review_resume",
                    resume_from=task.resume_from,
                    actor_id=DEFAULT_REVIEWER_ID,
                    reason=decision.reason,
                )
        self._record_attempt(task, "decided", decision, decision.reason)
        return decision

    def attempt_merge_review(self, task: MergeReviewTask) -> Optional[AutoReviewDecision]:
        """尝试自动审核 merge_review 任务。"""
        if self._has_attempt(task.id):
            return None
        if self.mode() != "agent" or "merge_review" not in self.settings.auto_review_enabled_types:
            return None
        decision = self._decide_merge(task)
        if decision is None or decision.escalate:
            if decision is not None:
                self._record_attempt(task, "escalated", decision, decision.reason)
            return None
        if decision.confidence < self._min_confidence():
            self._record_attempt(task, "escalated", decision, "置信度低于自动审核门槛")
            return None
        self._apply_merge_decision(task, decision)
        self._record_attempt(task, "decided", decision, decision.reason)
        return decision

    def _min_confidence(self) -> float:
        policy_getter = getattr(self.repository, "get_review_policy", None)
        policy = policy_getter() if callable(policy_getter) else None
        return float(
            getattr(policy, "min_confidence", None) or self.settings.auto_review_min_confidence
        )

    def _record_attempt(
        self,
        task: ReviewTask | MergeReviewTask,
        status: str,
        decision: Optional[AutoReviewDecision],
        reason: str,
    ) -> None:
        saver = getattr(self.repository, "save_auto_review_attempt", None)
        if not callable(saver):
            return
        saver(
            AutoReviewAttempt(
                id=new_id("ara"),
                task_id=task.id,
                object_type=getattr(task, "object_type", "merge_review"),
                object_id=getattr(task, "object_id", getattr(task, "document_id", task.id)),
                status=status,
                decision=decision.decision if decision else None,
                confidence=decision.confidence if decision else 0.0,
                reason=reason,
                model_run_id=decision.model_run_id if decision else None,
                created_at=datetime.now(timezone.utc),
                context=decision.context if decision else {},
            )
        )

    def _has_attempt(self, task_id: str) -> bool:
        getter = getattr(self.repository, "list_auto_review_attempts", None)
        return bool(getter(task_id, 1)) if callable(getter) else False

    # --- 决策规则 ---

    def _decide_report(self, task: ReviewTask, card: FactCard) -> Optional[AutoReviewDecision]:
        event = self.repository.get_event(card.event_id)
        claims = self.repository.get_claims_for_event(card.event_id) if event else []
        if any(claim.status == "conflicted" for claim in claims):
            return self._maybe_llm({"task_type": "report", "reason": "存在冲突 claim"})
        if event and event.missing_required:
            return self._maybe_llm(
                {
                    "task_type": "report",
                    "reason": "事件缺少必填字段",
                    "missing_required": event.missing_required,
                }
            )

        main_claim = self.repository.get_claim(card.claim_ids[0]) if card.claim_ids else None
        if main_claim is None:
            return self._maybe_llm({"task_type": "report", "reason": "无主 claim"})

        tier = self._source_tier_for_claim(main_claim)
        if self._tier_rank(tier) >= 2:
            return AutoReviewDecision(
                decision="approve",
                confidence=1.0,
                reason=f"高信任来源 {tier}，无冲突，无缺失字段",
                escalate=False,
            )

        return self._maybe_llm(
            {
                "task_type": "report",
                "source_tier": tier,
                "claim_statuses": [c.status for c in claims],
            }
        )

    def _decide_conflict(
        self, task: ReviewTask, conflict: ConflictRecord
    ) -> Optional[AutoReviewDecision]:
        if conflict.severity == "critical":
            return self._maybe_llm({"task_type": "claim_conflict", "reason": "critical severity"})

        claims = [self.repository.get_claim(claim_id) for claim_id in conflict.claim_ids]
        claims = [c for c in claims if c is not None]
        if len(claims) < 2:
            return self._maybe_llm({"task_type": "claim_conflict", "reason": "claim 数量不足"})

        tiers = [self._source_tier_for_claim(c) for c in claims]
        ranks = [self._tier_rank(t) for t in tiers]
        max_rank = max(ranks)
        min_rank = min(ranks)

        if max_rank - min_rank >= 2:
            winner_idx = ranks.index(max_rank)
            winner = claims[winner_idx]
            return AutoReviewDecision(
                decision="approve",
                confidence=1.0,
                reason=f"来源信任等级差距明显：{tiers[winner_idx]} 获胜",
                escalate=False,
                context={"winner_claim_ids": [winner.id]},
            )

        return self._maybe_llm(
            {
                "task_type": "claim_conflict",
                "conflict_type": conflict.conflict_type,
                "severity": conflict.severity,
                "source_tiers": tiers,
            }
        )

    def _decide_merge(self, task: MergeReviewTask) -> Optional[AutoReviewDecision]:
        decisions = self.repository.list_match_decisions(task.document_id)
        if not decisions:
            return self._maybe_llm({"task_type": "merge_review", "reason": "无 matcher 决策记录"})
        latest = max(decisions, key=lambda d: d.created_at or datetime.min.replace(tzinfo=None))
        score = float(latest.score or 0)
        if score <= 0.25:
            return AutoReviewDecision(
                decision="new_event",
                confidence=0.95,
                reason="matcher 分数极低，应视为新事件",
                escalate=False,
            )
        # merge 涉及事件合并，当前版本先交给人工；skip 暂也转人工。
        return self._maybe_llm(
            {
                "task_type": "merge_review",
                "matcher_score": score,
                "decision": latest.decision,
            }
        )

    def _maybe_llm(self, context: dict) -> Optional[AutoReviewDecision]:
        if not self.settings.auto_review_llm_fallback:
            return None
        return self.agent.decide(context)

    # --- 应用决定 ---

    def _apply_report_decision(
        self, task: ReviewTask, card: FactCard, decision: AutoReviewDecision
    ) -> None:
        if decision.decision != "approve":
            return
        replacement = replace(
            card,
            id=new_id("rpt"),
            version=card.version + 1,
            status="approved",
            supersedes_report_id=card.id,
            change_reason=f"auto-approved: {decision.reason}",
        )
        self.repository.save_fact_card(replacement)
        self._mark_task_decided(task, decision)

    def _apply_conflict_decision(
        self, task: ReviewTask, conflict: ConflictRecord, decision: AutoReviewDecision
    ) -> None:
        winner_ids = set(decision.context.get("winner_claim_ids", []))
        if not winner_ids and decision.decision == "approve":
            # 兜底：按来源等级重新选出胜者
            claims = [self.repository.get_claim(cid) for cid in conflict.claim_ids]
            claims = [c for c in claims if c is not None]
            if claims:
                winner = max(claims, key=lambda c: self._tier_rank(self._source_tier_for_claim(c)))
                winner_ids = {winner.id}

        for claim_id in conflict.claim_ids:
            claim = self.repository.get_claim(claim_id)
            if claim is None:
                continue
            next_status = "verified" if claim_id in winner_ids else "rejected"
            if claim.status == next_status:
                continue
            self.repository.update_claim(replace(claim, status=next_status))

        self.repository.update_conflict(
            replace(
                conflict,
                status="resolved",
                resolution=decision.decision,
                version=conflict.version + 1,
            )
        )
        self._mark_task_decided(task, decision)

    def _apply_merge_decision(self, task: MergeReviewTask, decision: AutoReviewDecision) -> None:
        updated = replace(
            task,
            status="decided",
            decision=decision.decision,
            reviewer_id=DEFAULT_REVIEWER_ID,
            decided_at=datetime.now(timezone.utc),
        )
        self.repository.update_merge_review_task(updated)
        self._audit("merge_review.auto_decided", task.id, decision)

    def _mark_task_decided(self, task: ReviewTask, decision: AutoReviewDecision) -> None:
        updated = replace(
            task,
            status="decided",
            decision=decision.decision,
            reviewer_id=DEFAULT_REVIEWER_ID,
            decided_at=datetime.now(timezone.utc),
            comment=decision.reason,
        )
        self.repository.update_review_task(updated)
        self._audit("review.auto_decided", task.id, decision)

    def _audit(self, action: str, object_id: str, decision: AutoReviewDecision) -> None:
        saver = getattr(self.repository, "save_audit_log", None)
        if not callable(saver):
            return
        saver(
            AuditLog(
                id=new_id("aud"),
                actor_id=DEFAULT_REVIEWER_ID,
                action=action,
                object_type="review_task",
                object_id=object_id,
                request_id=None,
                details={
                    "decision": decision.decision,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "context": decision.context,
                },
                created_at=datetime.now(timezone.utc),
            )
        )

    # --- 工具方法 ---

    @staticmethod
    def _tier_rank(tier: str) -> int:
        return {"S": 3, "A": 2, "B": 1, "C": 0}.get(tier, 0)

    def _source_tier_for_claim(self, claim: Claim) -> str:
        relations = self.repository.list_claim_evidence(claim.id)
        if not relations:
            return "C"
        best = "C"
        for relation in relations:
            evidence = self.repository.get_evidence(relation.evidence_id)
            if evidence is None:
                continue
            document = self.repository.get_document(evidence.document_id)
            if document is None:
                continue
            if self._tier_rank(document.source_tier) > self._tier_rank(best):
                best = document.source_tier
        return best
