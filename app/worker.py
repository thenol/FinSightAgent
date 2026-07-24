import argparse
import asyncio
import hashlib
import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.application.pipeline import EventResearchPipeline
from app.ingestion.artifacts import LocalArtifactStore
from app.ingestion.rss import RssFeedClient
from app.ingestion.scheduler import build_source_scheduler
from app.ingestion.sync import IngestSyncService
from app.platform.db_models import WorkflowRunModel
from app.platform.messaging import OutboxPublisher, RedisStreamBroker
from app.platform.repository import SqlAlchemyRepository
from app.platform.settings import Settings
from app.workflows.service import WorkflowService


async def run_outbox() -> None:
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        raise RuntimeError("Outbox worker requires FINSIGHT_REPOSITORY=postgresql")

    repository = SqlAlchemyRepository(settings.database_url)
    broker = RedisStreamBroker(settings.redis_url)
    publisher = OutboxPublisher(repository, broker)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)

    try:
        while not stop.is_set():
            result = await publisher.run_once()
            delay = 0.1 if result.published or result.failed else 1.0
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
    finally:
        await broker.close()


async def run_workflow() -> None:
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        raise RuntimeError("Workflow worker requires FINSIGHT_REPOSITORY=postgresql")
    repository = SqlAlchemyRepository(settings.database_url)
    service = WorkflowService(repository)
    stale_after = timedelta(
        seconds=max(1, int(os.getenv("FINSIGHT_WORKFLOW_STALE_SECONDS", "300")))
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)

    while not stop.is_set():
        with claim_workflow_run(repository, stale_after=stale_after) as workflow_id:
            if workflow_id is not None:
                await asyncio.to_thread(service.run, workflow_id)
                continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass


async def run_source() -> None:
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        raise RuntimeError("Source worker requires FINSIGHT_REPOSITORY=postgresql")

    repository = SqlAlchemyRepository(settings.database_url)
    client = RssFeedClient()
    sync_service = IngestSyncService(
        repository,
        EventResearchPipeline(repository, LocalArtifactStore(settings.artifact_root)),
        client=client,
        settings=settings,
    )
    scheduler = build_source_scheduler(repository, sync_service, settings=settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)

    scheduler.start()
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)
        await client.close()


@contextmanager
def claim_workflow_run(
    repository: SqlAlchemyRepository,
    *,
    stale_after: timedelta,
    now: datetime | None = None,
) -> Iterator[str | None]:
    """Atomically claim one run and hold a crash-safe PostgreSQL execution lock.

    The current repository contract has no workflow lease or heartbeat fields.
    ``created_at`` therefore only bounds stale candidates; the session-level
    advisory lock is the authoritative liveness signal and survives commits.
    PostgreSQL releases it automatically if the worker process/connection dies.
    """

    if repository.engine.dialect.name != "postgresql":
        raise RuntimeError("Workflow claiming requires PostgreSQL advisory locks")

    cutoff = (now or datetime.now(timezone.utc)) - stale_after
    connection = repository.engine.connect()
    lock_key: int | None = None
    workflow_id: str | None = None
    try:
        with Session(connection, expire_on_commit=False) as session:
            with session.begin():
                candidates = session.scalars(
                    select(WorkflowRunModel)
                    .where(
                        or_(
                            WorkflowRunModel.status == "pending",
                            (
                                (WorkflowRunModel.status == "running")
                                & (WorkflowRunModel.created_at <= cutoff)
                            ),
                        )
                    )
                    .order_by(
                        case((WorkflowRunModel.status == "pending", 0), else_=1),
                        WorkflowRunModel.created_at,
                        WorkflowRunModel.id,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(20)
                ).all()
                for candidate in candidates:
                    candidate_lock_key = _workflow_lock_key(candidate.id)
                    acquired = session.scalar(
                        select(func.pg_try_advisory_lock(candidate_lock_key))
                    )
                    if not acquired:
                        continue
                    lock_key = candidate_lock_key
                    workflow_id = candidate.id
                    candidate.status = "running"
                    session.flush()
                    break

        yield workflow_id
    finally:
        if lock_key is not None:
            connection.execute(select(func.pg_advisory_unlock(lock_key)))
            connection.commit()
        connection.close()


def _workflow_lock_key(workflow_id: str) -> int:
    digest = hashlib.sha256(f"finsight-workflow:{workflow_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="FinSightAgent background worker")
    parser.add_argument("worker", choices=["outbox", "workflow", "source"])
    arguments = parser.parse_args()
    if arguments.worker == "outbox":
        asyncio.run(run_outbox())
    if arguments.worker == "workflow":
        asyncio.run(run_workflow())
    if arguments.worker == "source":
        asyncio.run(run_source())


if __name__ == "__main__":
    main()
