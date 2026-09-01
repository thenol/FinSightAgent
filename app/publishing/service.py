import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from app.analysis.service import ImpactAnalysisService
from app.domain import Claim, Event, FactCard, ReviewTask, WorkflowRun
from app.platform.ids import new_id
from app.platform.repository import Repository
from app.platform.settings import Settings
from app.review.service import AutoReviewService

logger = logging.getLogger(__name__)


class FactCardService:
    def __init__(
        self,
        repository: Repository,
        settings: Optional[Settings] = None,
        impact_service: Optional[ImpactAnalysisService] = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or Settings.from_environment()
        self.impact_service = impact_service

    def create(self, event: Event, claim: Claim) -> FactCard:
        if claim.status == "verified":
            summary = f"已通过原始来源确认：{claim.subject_text} 披露 {event.event_type} 事件。"
            status = "published"
        elif claim.status == "conflicted":
            summary = f"检测到 {event.event_type} 事件的关键事实存在冲突，需人工审核。"
            status = "needs_review"
        else:
            summary = f"检测到 {event.event_type} 事件，但来源尚不足以完成事实验证。"
            status = "review_required"
        previous = self.repository.get_fact_card_for_event(event.id)
        card = FactCard(
            id=new_id("rpt"),
            event_id=event.id,
            version=previous.version + 1 if previous else 1,
            status=status,
            title=event.title,
            summary=summary,
            claim_ids=[claim.id],
            as_of=claim.as_of,
        )
        self.repository.save_fact_card(card)
        if status in {"review_required", "needs_review"}:
            reason_code = (
                "CLAIM_CONFLICT" if claim.status == "conflicted" else "REPORT_REVIEW_REQUIRED"
            )
            task = ReviewTask(
                id=new_id("rvt"),
                object_type="report",
                object_id=card.id,
                reason_code=reason_code,
                allowed_decisions=["approve", "return", "reject"],
                created_at=datetime.now(timezone.utc),
            )
            self.repository.save_review_task(task)
            AutoReviewService(self.repository).attempt_report_review(task, card)
        if card.status == "published":
            self._maybe_trigger_impact_analysis(event.id)
        return card

    def create_from_draft(
        self,
        event: Event,
        run: WorkflowRun,
        draft: dict,
        *,
        status: str = "needs_review",
        change_reason: str | None = None,
    ) -> FactCard:
        """追加保存由 ReportAssembler 生成的完整报告快照。

        ``draft`` 是跨模块的已版本化装配输出；这里刻意只提取受支持字段，
        以避免将任意 Blackboard 内容持久化为报告正文。
        """
        previous = self.repository.get_fact_card_for_event(event.id)
        fingerprint = (draft.get("provenance") or {}).get("semantic_fingerprint")
        if (
            fingerprint
            and previous is not None
            and (previous.provenance or {}).get("semantic_fingerprint") == fingerprint
        ):
            # A replay with the same evidence and memo must not create another
            # reader-visible report version or another review task.
            return previous
        card = FactCard(
            id=new_id("rpt"),
            event_id=event.id,
            version=previous.version + 1 if previous else 1,
            status=status,
            report_type=draft.get("report_type", "fact_card"),
            title=draft.get("title", event.title),
            summary=draft.get("summary", ""),
            claim_ids=list(draft.get("claim_ids", [])),
            as_of=run.as_of,
            disclaimer=draft.get("disclaimer", FactCard.__dataclass_fields__["disclaimer"].default),
            supersedes_report_id=previous.id if previous else None,
            change_reason=change_reason,
            content=dict(draft.get("content") or {}),
            provenance=dict(draft.get("provenance") or {"workflow_run_id": run.id}),
        )
        self.repository.save_fact_card(card)
        if card.status == "published":
            self._maybe_trigger_impact_analysis(event.id)
        return card

    def transition(self, card: FactCard, status: str, reason: str) -> FactCard:
        """Append a replacement version; historical reports are never updated."""
        replacement = replace(
            card,
            id=new_id("rpt"),
            version=card.version + 1,
            status=status,
            supersedes_report_id=card.id,
            change_reason=reason,
        )
        self.repository.save_fact_card(replacement)
        if status == "published":
            self._maybe_trigger_impact_analysis(card.event_id)
        return replacement

    def _maybe_trigger_impact_analysis(self, event_id: str) -> None:
        """事实卡片发布后，为高重要度事件自动生成影响分析。

        PostgreSQL 环境下写入 Outbox 由异步 worker 消费；内存/测试环境下同步生成。
        生成失败不会阻塞报告发布，由用户后续手动重新生成。
        """
        if not self.settings.auto_impact_analysis_enabled:
            return
        event = self.repository.get_event(event_id)
        if event is None:
            return
        if event.status in {"dormant", "archived", "cold"}:
            return
        if event.importance < self.settings.auto_impact_analysis_importance_threshold:
            return
        if self.repository.get_latest_impact_analysis_for_event(event_id) is not None:
            return
        if self.settings.repository == "postgresql":
            self.repository.add_outbox(
                "impact_analysis.requested.v1",
                event_id,
                {"event_id": event_id, "trigger": "fact_card.published"},
            )
            return
        service = self.impact_service or ImpactAnalysisService(self.repository, self.settings)
        try:
            service.generate(event_id, actor="system:auto")
        except Exception as exc:
            logger.warning(
                "auto impact analysis failed: event_id=%s type=%s error=%s",
                event_id,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
