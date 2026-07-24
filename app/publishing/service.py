from dataclasses import replace
from datetime import datetime, timezone

from app.domain import Claim, Event, FactCard, ReviewTask, WorkflowRun
from app.platform.ids import new_id
from app.platform.repository import Repository


class FactCardService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def create(self, event: Event, claim: Claim) -> FactCard:
        if claim.status == "verified":
            summary = f"已通过原始来源确认：{claim.subject_text} 披露 {event.event_type} 事件。"
            status = "published"
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
        if status == "review_required":
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
        return replacement
