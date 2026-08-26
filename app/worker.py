import argparse
import asyncio
import hashlib
import logging
import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.analysis.aggregation_worker import ImpactAggregationWorker
from app.analysis.backfill import ImpactProjectionBackfillService
from app.analysis.forward_worker import ForwardImpactWorker
from app.analysis.worker import ImpactAnalysisWorker
from app.application.pipeline import EventResearchPipeline
from app.events.reevaluation import ReevaluationService
from app.ingestion.artifacts import LocalArtifactStore
from app.ingestion.rss import RssFeedClient
from app.ingestion.scheduler import build_source_scheduler
from app.ingestion.sync import IngestSyncService
from app.market.adapters import (
    AkShareMarketDataProvider,
    EastMoneyBridgeMarketDataProvider,
    EastMoneyMarketDataProvider,
    FallbackMarketDataProvider,
)
from app.market.calendar import build_trading_calendar
from app.market.forecasting import ForecastLifecycleService
from app.market.master_data import seed_market_master_data
from app.market.reference import MarketInstrumentCatalog
from app.market.storage import build_market_batch_store
from app.market.worker import MarketDataWorker
from app.platform.db_models import WorkflowRunModel
from app.platform.messaging import OutboxPublisher, RedisStreamBroker
from app.platform.repository import InMemoryRepository, SqlAlchemyRepository
from app.platform.settings import Settings
from app.review.service import AutoReviewService
from app.workflows.service import WorkflowService

logger = logging.getLogger(__name__)


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


async def run_impact_aggregation() -> None:
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        raise RuntimeError("Impact aggregation worker requires FINSIGHT_REPOSITORY=postgresql")
    repository = SqlAlchemyRepository(settings.database_url)
    worker = ImpactAggregationWorker(repository)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)
    while not stop.is_set():
        processed = await asyncio.to_thread(worker.run_once, 20)
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.1 if processed else 1.0)
        except asyncio.TimeoutError:
            pass


def run_impact_backfill() -> None:
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        raise RuntimeError("Impact backfill requires FINSIGHT_REPOSITORY=postgresql")
    report = ImpactProjectionBackfillService(SqlAlchemyRepository(settings.database_url)).run()
    logger.info("impact projection backfill completed: %s", report)


async def run_forward_impact() -> None:
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        raise RuntimeError("Forward impact worker requires FINSIGHT_REPOSITORY=postgresql")
    repository = SqlAlchemyRepository(settings.database_url)
    worker = ForwardImpactWorker(repository)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)
    while not stop.is_set():
        processed = await asyncio.to_thread(worker.run_once, 10)
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.1 if processed else 1.0)
        except asyncio.TimeoutError:
            pass


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


def _auto_review_once(repository, limit: int = 20) -> int:
    service = AutoReviewService(repository)
    processed = 0
    for task in repository.list_review_tasks(status="pending", limit=limit):
        service.attempt_task(task)
        processed += 1
    for task in repository.list_merge_review_tasks(status="open", limit=limit):
        service.attempt_merge_review(task)
        processed += 1
    return processed


async def run_auto_review() -> None:
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        raise RuntimeError("Auto review worker requires FINSIGHT_REPOSITORY=postgresql")
    repository = SqlAlchemyRepository(settings.database_url)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)
    while not stop.is_set():
        processed = await asyncio.to_thread(_auto_review_once, repository, 20)
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.1 if processed else 2.0)
        except asyncio.TimeoutError:
            pass


async def run_impact_analysis() -> None:
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        raise RuntimeError("Impact analysis worker requires FINSIGHT_REPOSITORY=postgresql")
    repository = SqlAlchemyRepository(settings.database_url)
    worker = ImpactAnalysisWorker(repository, settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)
    await worker.run(stop, poll_interval=1.0)


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


async def run_reevaluate() -> None:
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        raise RuntimeError("Reevaluation worker requires FINSIGHT_REPOSITORY=postgresql")
    repository = SqlAlchemyRepository(settings.database_url)
    service = ReevaluationService(repository)
    interval = max(5, int(os.getenv("FINSIGHT_REEVALUATE_INTERVAL_SECONDS", "60")))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)

    while not stop.is_set():
        await asyncio.to_thread(service.run_once)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def run_ood_observation() -> None:
    """Validate queued OOD observations and keep the learning queue observable."""
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        raise RuntimeError("OOD observation worker requires FINSIGHT_REPOSITORY=postgresql")
    repository = SqlAlchemyRepository(settings.database_url)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)
    while not stop.is_set():
        observations = repository.list_ood_observations(status="ready_for_clustering", limit=50)
        logger.info("ood observation queue size=%s", len(observations))
        try:
            await asyncio.wait_for(stop.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


async def run_market_data() -> None:
    settings = Settings.from_environment()
    catalog = load_market_catalog(settings)
    eastmoney = EastMoneyMarketDataProvider(
        catalog.as_mapping(), timeout_seconds=settings.market_data_timeout_seconds
    )
    akshare = AkShareMarketDataProvider()
    bridge = EastMoneyBridgeMarketDataProvider(
        catalog.as_mapping(),
        base_url=settings.market_data_bridge_url,
        timeout_seconds=settings.market_data_timeout_seconds,
    )
    if settings.market_data_provider == "eastmoney":
        provider = eastmoney
    elif settings.market_data_provider in {"bridge", "auto"}:
        provider = bridge
    elif settings.market_data_provider == "akshare":
        provider = akshare
    elif settings.market_data_provider == "none":
        raise RuntimeError("Market data worker requires a configured provider")
    else:
        provider = FallbackMarketDataProvider(eastmoney, akshare)
    instrument_ids = market_instrument_ids(catalog)
    worker = MarketDataWorker(
        provider,
        build_market_batch_store(
            mode=settings.market_data_store,
            archive_root=settings.market_archive_root,
            clickhouse_url=settings.clickhouse_url,
        ),
        instrument_ids=instrument_ids,
        interval=os.getenv("MARKET_DATA_INTERVAL", "1d"),
        lookback_days=max(1, int(os.getenv("MARKET_DATA_LOOKBACK_DAYS", "45"))),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)
    await worker.run_forever(
        stop,
        interval_seconds=max(5, int(os.getenv("MARKET_DATA_WORKER_INTERVAL_SECONDS", "300"))),
    )


async def run_forecast_outcomes() -> None:
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        raise RuntimeError("Forecast outcome worker requires FINSIGHT_REPOSITORY=postgresql")
    repository = SqlAlchemyRepository(settings.database_url)
    catalog = seed_market_master_data(repository)
    eastmoney = EastMoneyMarketDataProvider(
        catalog.as_mapping(), timeout_seconds=settings.market_data_timeout_seconds
    )
    akshare = AkShareMarketDataProvider()
    bridge = EastMoneyBridgeMarketDataProvider(
        catalog.as_mapping(),
        base_url=settings.market_data_bridge_url,
        timeout_seconds=settings.market_data_timeout_seconds,
    )
    if settings.market_data_provider in {"bridge", "auto"}:
        provider = bridge
    elif settings.market_data_provider == "eastmoney":
        provider = eastmoney
    elif settings.market_data_provider == "akshare":
        provider = akshare
    elif settings.market_data_provider == "none":
        raise RuntimeError("Forecast outcome worker requires a configured market provider")
    else:
        provider = FallbackMarketDataProvider(eastmoney, akshare)
    service = ForecastLifecycleService(repository, provider, catalog, build_trading_calendar())
    interval = max(60, int(os.getenv("FORECAST_OUTCOME_WORKER_INTERVAL_SECONDS", "3600")))
    flat_band = max(0.0, float(os.getenv("FORECAST_OUTCOME_FLAT_BAND", "0.003")))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)
    while not stop.is_set():
        receipt = await asyncio.to_thread(
            service.settle,
            evaluation_as_of=datetime.now(timezone.utc),
            flat_band=flat_band,
        )
        logger.info(
            "forecast outcome settlement considered=%s settled=%s pending=%s excluded=%s",
            receipt.considered_count,
            receipt.settled_count,
            receipt.pending_count,
            receipt.excluded_count,
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


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
                    acquired = session.scalar(select(func.pg_try_advisory_lock(candidate_lock_key)))
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


def load_market_catalog(
    settings: Settings, repository=None
) -> MarketInstrumentCatalog:
    """Load the persisted instrument catalog; seed bootstrap rows if the table is empty.

    Production workers must not query the in-code DEFAULT_INSTRUMENTS list directly.
    That tuple remains the idempotent seed source used by ``seed_market_master_data``.
    """
    if repository is None:
        if settings.repository == "postgresql":
            repository = SqlAlchemyRepository(settings.database_url)
        else:
            repository = InMemoryRepository()
    return seed_market_master_data(repository)


def market_instrument_ids(catalog: MarketInstrumentCatalog) -> tuple[str, ...]:
    configured = tuple(
        item.strip()
        for item in os.getenv("MARKET_DATA_INSTRUMENT_IDS", "").split(",")
        if item.strip()
    )
    if configured:
        missing = [item for item in configured if catalog.get(item) is None]
        if missing:
            raise RuntimeError(f"Market instruments are not in master data: {missing}")
        return configured
    ids = tuple(item.id for item in catalog.list())
    if not ids:
        raise RuntimeError("Market master data catalog is empty")
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="FinSightAgent background worker")
    parser.add_argument(
        "worker",
        choices=[
            "outbox",
            "workflow",
            "auto-review",
            "source",
            "impact-analysis",
            "impact-aggregation",
            "impact-backfill",
            "forward-impact",
            "reevaluate",
            "market-data",
            "forecast-outcomes",
            "ood-observation",
        ],
    )
    arguments = parser.parse_args()
    if arguments.worker == "outbox":
        asyncio.run(run_outbox())
    if arguments.worker == "workflow":
        asyncio.run(run_workflow())
    if arguments.worker == "auto-review":
        asyncio.run(run_auto_review())
    if arguments.worker == "source":
        asyncio.run(run_source())
    if arguments.worker == "impact-analysis":
        asyncio.run(run_impact_analysis())
    if arguments.worker == "impact-aggregation":
        asyncio.run(run_impact_aggregation())
    if arguments.worker == "impact-backfill":
        run_impact_backfill()
    if arguments.worker == "forward-impact":
        asyncio.run(run_forward_impact())
    if arguments.worker == "reevaluate":
        asyncio.run(run_reevaluate())
    if arguments.worker == "market-data":
        asyncio.run(run_market_data())
    if arguments.worker == "forecast-outcomes":
        asyncio.run(run_forecast_outcomes())
    if arguments.worker == "ood-observation":
        asyncio.run(run_ood_observation())


if __name__ == "__main__":
    main()
