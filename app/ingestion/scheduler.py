"""Source ingest scheduler (MarketMind-style APScheduler + rescan).

- One IntervalTrigger job per active source (crawl_interval_seconds).
- Rescan every RESCAN_SECONDS to add/remove sources and refresh intervals.
- Intended to run in `python -m app.worker source`, not inside the API process.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.domain import Source
from app.ingestion.sync import IngestSyncService
from app.platform.repository import RepositoryProvider
from app.platform.retention import purge_expired_documents
from app.platform.settings import Settings

logger = logging.getLogger("finsight.source_scheduler")

RESCAN_SECONDS = 60
SyncCallable = Callable[[Source], Awaitable[dict]]


def _job_id(source_id: str) -> str:
    return f"ingest:{source_id}"


def _scheduled_ingest_ids(scheduler: AsyncIOScheduler) -> set[str]:
    return {job.id for job in scheduler.get_jobs() if str(job.id).startswith("ingest:")}


def _interval_for(source: Source) -> int:
    return max(60, int(source.crawl_interval_seconds or 3600))


async def _job_ingest(
    source_id: str,
    repository: RepositoryProvider,
    sync_service: IngestSyncService,
) -> None:
    source = repository.get_source(source_id)
    if source is None or source.status != "active":
        return
    try:
        await sync_service.sync(source, trigger="scheduled")
    except Exception:  # noqa: BLE001
        logger.exception("scheduled_ingest_failed source_id=%s", source_id)


async def rescan_sources(
    scheduler: AsyncIOScheduler,
    repository: RepositoryProvider,
    sync_service: IngestSyncService,
) -> None:
    """Sync DB active sources into the scheduler; refresh changed intervals."""
    try:
        sources = [item for item in repository.list_sources() if item.status == "active"]
        wanted = {_job_id(item.id): item for item in sources}
        current = _scheduled_ingest_ids(scheduler)

        for job_id in current - set(wanted):
            scheduler.remove_job(job_id)
            logger.info("unscheduled_source job=%s", job_id)

        for job_id, source in wanted.items():
            interval = _interval_for(source)
            existing = scheduler.get_job(job_id)
            if existing is None:
                scheduler.add_job(
                    _job_ingest,
                    trigger=IntervalTrigger(seconds=interval),
                    id=job_id,
                    args=[source.id, repository, sync_service],
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1,
                )
                logger.info("scheduled_source source_id=%s interval=%s", source.id, interval)
                continue
            # Rebuild when interval changed.
            trigger = existing.trigger
            current_interval = getattr(trigger, "interval", None)
            current_seconds = (
                int(current_interval.total_seconds()) if current_interval is not None else None
            )
            if current_seconds != interval:
                scheduler.add_job(
                    _job_ingest,
                    trigger=IntervalTrigger(seconds=interval),
                    id=job_id,
                    args=[source.id, repository, sync_service],
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1,
                )
                logger.info(
                    "rescheduled_source source_id=%s interval=%s", source.id, interval
                )
    except Exception:  # noqa: BLE001
        logger.exception("rescan_sources_failed")


async def _job_auto_purge(
    repository: RepositoryProvider,
    settings: Settings,
) -> None:
    try:
        await asyncio.to_thread(
            purge_expired_documents,
            repository,
            min_soft_delete_age_seconds=settings.document_purge_min_age_seconds,
            limit=settings.document_purge_batch_size,
        )
    except Exception:  # noqa: BLE001
        logger.exception("scheduled_auto_purge_failed")


def build_source_scheduler(
    repository: RepositoryProvider,
    sync_service: IngestSyncService,
    *,
    rescan_seconds: int = RESCAN_SECONDS,
    settings: Settings | None = None,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(
        jobstores={"default": MemoryJobStore()},
        job_defaults={"coalesce": True, "max_instances": 1},
    )
    for source in repository.list_sources():
        if source.status != "active":
            continue
        interval = _interval_for(source)
        scheduler.add_job(
            _job_ingest,
            trigger=IntervalTrigger(seconds=interval),
            id=_job_id(source.id),
            args=[source.id, repository, sync_service],
            replace_existing=True,
        )
        logger.info("scheduled_source source_id=%s interval=%s", source.id, interval)

    scheduler.add_job(
        rescan_sources,
        trigger=IntervalTrigger(seconds=max(15, rescan_seconds)),
        id="rescan:sources",
        args=[scheduler, repository, sync_service],
        replace_existing=True,
    )
    logger.info("scheduled_rescan interval=%s", rescan_seconds)

    runtime = settings or Settings.from_environment()
    purge_interval = int(runtime.document_purge_interval_seconds)
    if purge_interval > 0:
        scheduler.add_job(
            _job_auto_purge,
            trigger=IntervalTrigger(seconds=max(60, purge_interval)),
            id="retention:auto_purge",
            args=[repository, runtime],
            replace_existing=True,
        )
        logger.info("scheduled_auto_purge interval=%s", purge_interval)
    return scheduler
