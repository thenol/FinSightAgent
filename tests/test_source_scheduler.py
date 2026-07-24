"""Source scheduler and ingest_run persistence."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx

from app.application.pipeline import EventResearchPipeline
from app.domain import Source
from app.ingestion.guard import FetchGuard
from app.ingestion.rss import RssFeedClient
from app.ingestion.scheduler import build_source_scheduler, rescan_sources
from app.ingestion.sync import IngestSyncService
from app.platform.repository import InMemoryRepository
from app.platform.settings import Settings


def _service(repository, transport=None) -> IngestSyncService:
    settings = replace(Settings.from_environment(), robots_enabled=False)
    client = RssFeedClient(
        httpx.AsyncClient(transport=transport or httpx.MockTransport(lambda _: httpx.Response(503)))
    )
    return IngestSyncService(
        repository,
        EventResearchPipeline(repository),
        client=client,
        guard=FetchGuard(settings=settings),
    )


def test_sync_writes_skipped_ingest_run_for_disabled_source() -> None:
    repository = InMemoryRepository()
    source = Source(
        id="src_disabled",
        code="disabled",
        name="Disabled",
        trust_tier="A",
        feed_url="https://source.example.com/feed.xml",
        allowed_domains=["source.example.com"],
        status="disabled",
    )
    repository.save_source(source)
    result = asyncio.run(_service(repository).sync(source, trigger="manual"))
    assert result["status"] == "disabled"
    assert result["run_status"] == "skipped"
    runs = repository.list_ingest_runs(source.id)
    assert len(runs) == 1
    assert runs[0].status == "skipped"
    assert runs[0].trigger == "manual"


def test_sync_writes_skipped_ingest_run_for_backoff() -> None:
    repository = InMemoryRepository()
    source = Source(
        id="src_backoff",
        code="backoff",
        name="Backoff",
        trust_tier="A",
        feed_url="https://source.example.com/feed.xml",
        allowed_domains=["source.example.com"],
        next_retry_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    repository.save_source(source)
    result = asyncio.run(_service(repository).sync(source, trigger="scheduled"))
    assert result["status"] == "backoff"
    runs = repository.list_ingest_runs(source.id)
    assert runs[0].status == "skipped"
    assert runs[0].trigger == "scheduled"


def test_scheduler_rescan_adds_removes_and_updates_interval() -> None:
    repository = InMemoryRepository()
    active = Source(
        id="src_a",
        code="a",
        name="A",
        trust_tier="A",
        feed_url="https://a.example.com/feed.xml",
        allowed_domains=["a.example.com"],
        crawl_interval_seconds=120,
    )
    inactive = Source(
        id="src_b",
        code="b",
        name="B",
        trust_tier="A",
        feed_url="https://b.example.com/feed.xml",
        allowed_domains=["b.example.com"],
        status="disabled",
        crawl_interval_seconds=120,
    )
    repository.save_source(active)
    repository.save_source(inactive)
    sync_service = _service(repository)
    sync_service.sync = AsyncMock(return_value={"fetched": 0})  # type: ignore[method-assign]

    scheduler = build_source_scheduler(repository, sync_service, rescan_seconds=60)
    assert scheduler.get_job("ingest:src_a") is not None
    assert scheduler.get_job("ingest:src_b") is None

    repository.update_source(replace(active, status="disabled"))
    asyncio.run(rescan_sources(scheduler, repository, sync_service))
    assert scheduler.get_job("ingest:src_a") is None

    revived = replace(active, status="active", crawl_interval_seconds=300)
    repository.update_source(revived)
    asyncio.run(rescan_sources(scheduler, repository, sync_service))
    job = scheduler.get_job("ingest:src_a")
    assert job is not None
    assert int(job.trigger.interval.total_seconds()) == 300
