"""Lightweight persisted worker liveness signals for the admin system view."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domain import AuditLog
from app.platform.ids import new_id

_LAST_SENT: dict[str, datetime] = {}


def record_worker_heartbeat(repository, worker_name: str, *, state: str = "running") -> None:
    now = datetime.now(timezone.utc)
    previous = _LAST_SENT.get(worker_name)
    if state == "running" and previous and now - previous < timedelta(seconds=30):
        return
    _LAST_SENT[worker_name] = now
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=None,
            action="system.worker.heartbeat",
            object_type="worker",
            object_id=worker_name,
            details={"state": state},
            request_id=None,
            created_at=now,
        )
    )
