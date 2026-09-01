"""每日 Top-N 简报。

BriefService 从当日已发布且允许进入简报的报告生成去重 Top-N 简报（DD-60 §8）。
排序分：
    brief_score = 0.40 * importance + 0.20 * urgency + 0.20 * confidence
                + 0.10 * novelty + 0.10 * recency

规则：
- 同一 Event 只保留最新版本。
- 同一公司默认最多两条，除非存在 critical 事件。
- 候选类型（Router 开放分类、待人工确认）事件不进入简报（DD-21 §2.4）。
- Brief 保存候选集、分数、规则版本和最终顺序，不重新调用研究 Agent（稳定重放）。
"""

from datetime import date, datetime, timedelta, timezone

from app.domain import Brief, BriefEntry, Event, FactCard
from app.events.schemas import is_candidate_event_type
from app.platform.ids import new_id
from app.platform.repository import Repository

BRIEF_RULE_VERSION = "brief-v1"
DEFAULT_TOP_N = 10
DEFAULT_COMPANY_LIMIT = 2

URGENCY_WEIGHT = {
    "critical": 1.0,
    "high": 0.8,
    "normal": 0.5,
    "low": 0.2,
}


class BriefService:
    def __init__(self, repository: Repository, top_n: int = DEFAULT_TOP_N) -> None:
        self.repository = repository
        self.top_n = top_n

    def generate(self, brief_date: date) -> Brief:
        """生成指定日期的 Top-N 简报。已存在则重放（稳定重放）。"""
        existing = self.repository.get_brief_by_date(brief_date.isoformat())
        if existing is not None:
            return existing

        start = datetime.combine(brief_date, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        reports = self.repository.list_published_reports(start, end)
        events = {e.id: e for e in self.repository.list_events()}

        entries = self._rank(reports, events)
        brief = Brief(
            id=new_id("brf"),
            brief_date=brief_date.isoformat(),
            entries=entries[: self.top_n],
            candidate_count=len(entries),
            rule_version=BRIEF_RULE_VERSION,
            generated_at=datetime.now(timezone.utc),
        )
        self.repository.save_brief(brief)
        return brief

    def _rank(self, reports: list[FactCard], events: dict[str, Event]) -> list[BriefEntry]:
        # 同一 Event 只保留最新版本（按 version 降序取首）
        latest_per_event: dict[str, FactCard] = {}
        for report in reports:
            current = latest_per_event.get(report.event_id)
            if current is None or report.version > current.version:
                latest_per_event[report.event_id] = report

        scored: list[tuple[BriefEntry, Event]] = []
        for report in latest_per_event.values():
            event = events.get(report.event_id)
            if event is None:
                continue
            # 候选类型事件（Router 开放分类、待人工确认）不进每日简报（DD-21 §2.4）。
            # 已升格为 accepted 的开放标签可以进入简报。
            if is_candidate_event_type(event.event_type):
                registry = self.repository.get_event_type_registry(event.event_type)
                if registry is None or registry.status != "accepted":
                    continue
            # cold/archived 事件不进简报（DD-22 §2.2）
            if event.status in {"cold", "archived"}:
                continue
            entry = self._score_entry(report, event)
            scored.append((entry, event))

        # 按 score 降序排序，稳定（同分按 event_id 保证可重放）
        scored.sort(key=lambda item: (-item[0].score, item[1].id))

        # 同一公司最多 DEFAULT_COMPANY_LIMIT 条，critical 除外
        capped = self._apply_company_cap(scored)

        return [entry for entry, _ in capped]

    def _score_entry(self, report: FactCard, event: Event) -> BriefEntry:
        importance = float(event.importance)
        urgency = URGENCY_WEIGHT.get(event.urgency, 0.5)
        confidence = self._report_confidence(report)
        novelty = self._novelty(report)
        recency = self._recency(report)
        score = round(
            0.40 * importance
            + 0.20 * urgency
            + 0.20 * confidence
            + 0.10 * novelty
            + 0.10 * recency,
            4,
        )
        return BriefEntry(
            report_id=report.id,
            event_id=report.event_id,
            entity_ids=list(event.entity_ids),
            title=report.title,
            importance=importance,
            urgency=event.urgency,
            confidence=confidence,
            novelty=novelty,
            recency=recency,
            score=score,
            rank=0,
        )

    def _report_confidence(self, report: FactCard) -> float:
        # 研究卡片置信度由报告承载；事实卡片回退到来源等级映射
        if report.report_type in {"research_card", "research_memo"}:
            memo = (report.content or {}).get("memo", {})
            if isinstance(memo, dict) and memo.get("confidence") is not None:
                return float(memo["confidence"])
            return 0.65
        return 0.50

    @staticmethod
    def _novelty(report: FactCard) -> float:
        # 新创建报告 novelty 高，替代版本（supersedes）较低
        return 1.0 if report.version == 1 else 0.40

    @staticmethod
    def _recency(report: FactCard) -> float:
        if report.as_of is None:
            return 0.50
        now = datetime.now(timezone.utc)
        delta_days = (now - report.as_of).total_seconds() / 86400
        if delta_days <= 0:
            return 1.0
        if delta_days >= 7:
            return 0.20
        return round(1.0 - delta_days / 7 * 0.80, 4)

    def _apply_company_cap(
        self, scored: list[tuple[BriefEntry, Event]]
    ) -> list[tuple[BriefEntry, Event]]:
        """同一公司默认最多 2 条，critical 事件不受配额限制。"""
        company_counts: dict[str, int] = {}
        result: list[tuple[BriefEntry, Event]] = []
        for entry, event in scored:
            company = event.entity_ids[0] if event.entity_ids else None
            has_critical = event.urgency == "critical"
            if company is not None and not has_critical:
                count = company_counts.get(company, 0)
                if count >= DEFAULT_COMPANY_LIMIT:
                    continue
                company_counts[company] = count + 1
            result.append((entry, event))
        # 重新编号 rank
        return [(replace_rank(entry, i + 1), event) for i, (entry, event) in enumerate(result)]


def replace_rank(entry: BriefEntry, rank: int) -> BriefEntry:
    from dataclasses import replace

    return replace(entry, rank=rank)
