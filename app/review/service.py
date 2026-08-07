from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from app.domain import (
    DEFAULT_REVIEWER_ID,
    AuditLog,
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

    def attempt_report_review(
        self, task: ReviewTask, card: FactCard
    ) -> Optional[AutoReviewDecision]:
        """尝试自动审核 report 类型任务。"""
        if "report" not in self.settings.auto_review_enabled_types:
            return None
        decision = self._decide_report(task, card)
        if decision is None or decision.escalate:
            return None
        if decision.confidence < self.settings.auto_review_min_confidence:
            return None
        self._apply_report_decision(task, card, decision)
        return decision

    def attempt_conflict_review(
        self, task: ReviewTask, conflict: ConflictRecord
    ) -> Optional[AutoReviewDecision]:
        """尝试自动审核 claim_conflict 类型任务。"""
        if "claim_conflict" not in self.settings.auto_review_enabled_types:
            return None
        decision = self._decide_conflict(task, conflict)
        if decision is None or decision.escalate:
            return None
        if decision.confidence < self.settings.auto_review_min_confidence:
            return None
        self._apply_conflict_decision(task, conflict, decision)
        return decision

    def attempt_workflow_review(self, task: ReviewTask) -> None:
        """workflow 类型暂不适合自动决定，统一转人工。"""
        return None

    def attempt_merge_review(
        self, task: MergeReviewTask
    ) -> Optional[AutoReviewDecision]:
        """尝试自动审核 merge_review 任务。"""
        if "merge_review" not in self.settings.auto_review_enabled_types:
            return None
        decision = self._decide_merge(task)
        if decision is None or decision.escalate:
            return None
        if decision.confidence < self.settings.auto_review_min_confidence:
            return None
        self._apply_merge_decision(task, decision)
        return decision

    # --- 决策规则 ---

    def _decide_report(self, task: ReviewTask, card: FactCard) -> Optional[AutoReviewDecision]:
        event = self.repository.get_event(card.event_id)
        claims = self.repository.get_claims_for_event(card.event_id) if event else []
        if any(claim.status == "conflicted" for claim in claims):
            return self._maybe_llm(
                {"task_type": "report", "reason": "存在冲突 claim"}
            )
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
            return self._maybe_llm(
                {"task_type": "claim_conflict", "reason": "critical severity"}
            )

        claims = [
            self.repository.get_claim(claim_id) for claim_id in conflict.claim_ids
        ]
        claims = [c for c in claims if c is not None]
        if len(claims) < 2:
            return self._maybe_llm(
                {"task_type": "claim_conflict", "reason": "claim 数量不足"}
            )

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
            return self._maybe_llm(
                {"task_type": "merge_review", "reason": "无 matcher 决策记录"}
            )
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
            claims = [
                self.repository.get_claim(cid)
                for cid in conflict.claim_ids
            ]
            claims = [c for c in claims if c is not None]
            if claims:
                winner = max(
                    claims, key=lambda c: self._tier_rank(self._source_tier_for_claim(c))
                )
                winner_ids = {winner.id}

        for claim_id in conflict.claim_ids:
            claim = self.repository.get_claim(claim_id)
            if claim is None:
                continue
            next_status = "verified" if claim_id in winner_ids else "rejected"
            if claim.status == next_status:
                continue
            self.repository.update_claim(
                replace(claim, status=next_status)
            )

        self.repository.update_conflict(
            replace(
                conflict,
                status="resolved",
                resolution=decision.decision,
                version=conflict.version + 1,
            )
        )
        self._mark_task_decided(task, decision)

    def _apply_merge_decision(
        self, task: MergeReviewTask, decision: AutoReviewDecision
    ) -> None:
        updated = replace(
            task,
            status="decided",
            decision=decision.decision,
            reviewer_id=DEFAULT_REVIEWER_ID,
            decided_at=datetime.now(timezone.utc),
        )
        self.repository.update_merge_review_task(updated)
        self._audit("merge_review.auto_decided", task.id, decision)

    def _mark_task_decided(
        self, task: ReviewTask, decision: AutoReviewDecision
    ) -> None:
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
