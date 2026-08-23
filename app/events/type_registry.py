"""事件类型注册表治理（DD-21 §2.4）。

一等词表外的开放分类标签在此登记：candidate → accepted / rejected。
计数在事件落库时累加；达阈值后列表接口标记 promotion_ready。
"""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from app.domain import EventTypeRegistryEntry
from app.platform.repository import Repository

CANDIDATE_TYPE_CONFIRMATION = "candidate_type_confirmation"


class EventTypeNotFoundError(KeyError):
    """Raised when a type_label is not in the registry."""


class EventTypeAlreadyDecidedError(ValueError):
    """Raised when accept/reject is called on a non-candidate entry."""


class EventTypeRegistryService:
    def __init__(self, repository: Repository, promotion_threshold: int = 5) -> None:
        self.repository = repository
        self.promotion_threshold = promotion_threshold

    def get(self, type_label: str) -> Optional[EventTypeRegistryEntry]:
        return self.repository.get_event_type_registry(type_label)

    def list_entries(self, status: Optional[str] = None) -> list[EventTypeRegistryEntry]:
        return self.repository.list_event_type_registry(status=status)

    def is_promotion_ready(self, entry: EventTypeRegistryEntry) -> bool:
        return entry.status == "candidate" and entry.event_count >= self.promotion_threshold

    def accept(self, type_label: str, decided_by: str) -> EventTypeRegistryEntry:
        return self._decide(type_label, "accepted", decided_by)

    def reject(self, type_label: str, decided_by: str) -> EventTypeRegistryEntry:
        return self._decide(type_label, "rejected", decided_by)

    def _decide(
        self, type_label: str, status: str, decided_by: str
    ) -> EventTypeRegistryEntry:
        entry = self.repository.get_event_type_registry(type_label)
        if entry is None:
            raise EventTypeNotFoundError(type_label)
        if entry.status != "candidate":
            raise EventTypeAlreadyDecidedError(type_label)
        now = datetime.now(timezone.utc)
        updated = replace(
            entry,
            status=status,
            decided_by=decided_by,
            decided_at=now,
            updated_at=now,
        )
        self.repository.save_event_type_registry(updated)
        return updated
