"""Document retention jobs (IMP-052).

Soft-delete is the archive step; this module auto-purges expired soft-deleted
documents whose retention hold is clear and whose soft-delete age exceeds the
configured window.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.platform.repository import (
    DocumentNotSoftDeletedError,
    PurgeRetentionWindowError,
    Repository,
    RetentionHoldError,
)

logger = logging.getLogger("finsight.retention")


def purge_expired_documents(
    repository: Repository,
    *,
    min_soft_delete_age_seconds: int,
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Purge soft-deleted documents past the retention window.

    Returns counts: scanned / purged / skipped / errors.
    """
    when = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    cutoff = when - timedelta(seconds=max(0, min_soft_delete_age_seconds))
    candidates = repository.list_documents_eligible_for_purge(
        deleted_before=cutoff, limit=max(1, limit)
    )
    purged_ids: list[str] = []
    skipped = 0
    errors = 0
    for document in candidates:
        try:
            repository.purge_document(
                document.id,
                purged_at=when,
                min_soft_delete_age_seconds=min_soft_delete_age_seconds,
            )
            purged_ids.append(document.id)
        except (RetentionHoldError, DocumentNotSoftDeletedError, PurgeRetentionWindowError):
            skipped += 1
        except Exception:  # noqa: BLE001
            errors += 1
            logger.exception("auto_purge_failed document_id=%s", document.id)
    result = {
        "scanned": len(candidates),
        "purged": len(purged_ids),
        "skipped": skipped,
        "errors": errors,
        "purged_ids": purged_ids,
        "cutoff": cutoff.isoformat(),
    }
    if purged_ids or errors:
        logger.info(
            "auto_purge_complete scanned=%s purged=%s skipped=%s errors=%s",
            result["scanned"],
            result["purged"],
            result["skipped"],
            result["errors"],
        )
    return result
