"""冷/休眠事件的监听重估服务（DD-22 §2.4）。

ReevaluationService 周期扫描 armed 状态的 WatchTrigger，用确定性只读查询
检查触发条件；命中即把事件从 cold/dormant 升级为 needs_review（等待人工确认
是否正式进入研究管道），触发器标记 fired 并留下审计证据。

重估是常态运行，不是"复活"特例：事件从未被丢弃，只是分析深度随信号连续变化。
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain import AuditLog, Event, WatchTrigger
from app.platform.ids import new_id
from app.platform.repository import Repository

REEVALUATION_RULE_VERSION = "reevaluation-v1"

# 来源信任等级排序：数值越大越权威
_TIER_RANK = {"S": 4, "A": 3, "B": 2, "C": 1}

# 可重估升级的状态；triaged/needs_review 已在管道内，archived 是人工终态
_REEVALUABLE_STATUSES = frozenset({"cold", "dormant"})


@dataclass(frozen=True)
class ReevaluationResult:
    scanned: int
    fired: int
    upgraded_event_ids: tuple[str, ...]


class ReevaluationService:
    """扫描 armed 触发器，命中条件即升级事件状态并留审计。"""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def run_once(self, *, limit: Optional[int] = None) -> ReevaluationResult:
        triggers = self.repository.list_watch_triggers(status="armed", limit=limit)
        fired = 0
        upgraded: list[str] = []
        for trigger in triggers:
            event = self.repository.get_event(trigger.event_id)
            if event is None or event.status not in _REEVALUABLE_STATUSES:
                # 事件已不在可重估状态（已升级/人工归档）：取消监听，避免悬挂
                self.repository.update_watch_trigger(replace(trigger, status="cancelled"))
                continue
            evidence = self._check(trigger, event)
            if evidence is None:
                continue
            self._fire(trigger, event, evidence)
            fired += 1
            upgraded.append(event.id)
        return ReevaluationResult(
            scanned=len(triggers),
            fired=fired,
            upgraded_event_ids=tuple(upgraded),
        )

    def _check(self, trigger: WatchTrigger, event: Event) -> Optional[dict[str, Any]]:
        if trigger.trigger_type == "source_cluster":
            return self._check_source_cluster(trigger, event)
        if trigger.trigger_type == "source_upgrade":
            return self._check_source_upgrade(trigger, event)
        return None  # market_signal / user_query：后续迭代

    def _event_source_tiers(self, event: Event) -> dict[str, str]:
        """event 关联文档的 source_id -> tier 映射。"""
        tiers: dict[str, str] = {}
        for document_id in event.document_ids:
            document = self.repository.get_document(document_id)
            if document is not None:
                tiers[document.source_id] = document.source_tier
        return tiers

    def _check_source_cluster(
        self, trigger: WatchTrigger, event: Event
    ) -> Optional[dict[str, Any]]:
        min_sources = int(trigger.condition.get("min_sources", 3))
        tiers = self._event_source_tiers(event)
        if len(tiers) < min_sources:
            return None
        return {
            "min_sources": min_sources,
            "actual_sources": len(tiers),
            "source_ids": sorted(tiers),
        }

    def _check_source_upgrade(
        self, trigger: WatchTrigger, event: Event
    ) -> Optional[dict[str, Any]]:
        baseline = str(trigger.condition.get("baseline_tier", "C"))
        baseline_rank = _TIER_RANK.get(baseline, 1)
        tiers = self._event_source_tiers(event)
        better = {
            source_id: tier
            for source_id, tier in tiers.items()
            if _TIER_RANK.get(tier, 1) > baseline_rank
        }
        if not better:
            return None
        return {
            "baseline_tier": baseline,
            "upgraded_sources": dict(sorted(better.items())),
        }

    def _fire(
        self, trigger: WatchTrigger, event: Event, evidence: dict[str, Any]
    ) -> None:
        now = datetime.now(timezone.utc)
        self.repository.update_watch_trigger(
            replace(trigger, status="fired", fired_at=now)
        )
        missing = sorted(set(event.missing_required or []) | {"reevaluation_confirm"})
        self.repository.update_event(
            replace(event, status="needs_review", missing_required=missing)
        )
        self.repository.save_audit_log(
            AuditLog(
                id=new_id("aud"),
                actor_id="system",
                action="event.reevaluated",
                object_type="event",
                object_id=event.id,
                request_id=None,
                details={
                    "trigger_id": trigger.id,
                    "trigger_type": trigger.trigger_type,
                    "previous_status": event.status,
                    "new_status": "needs_review",
                    "evidence": evidence,
                    "rule_version": REEVALUATION_RULE_VERSION,
                },
                created_at=now,
            )
        )
