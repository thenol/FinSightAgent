import asyncio
from dataclasses import replace

import httpx

from app.application.pipeline import EventResearchPipeline
from app.domain import Source
from app.ingestion.guard import FetchGuard
from app.ingestion.rss import RssFeedClient
from app.ingestion.sync import IngestSyncService
from app.platform.repository import InMemoryRepository
from app.platform.settings import Settings

RSS = (
    b"<rss><channel><item><guid>sync-1</guid><title>"
    b"Example 000001.SZ earnings guidance</title><link>"
    b"https://source.example.com/notice/1</link><pubDate>"
    b"Sun, 12 Jul 2026 09:30:00 GMT</pubDate></item></channel></rss>"
)


def _sync_service(repository, transport) -> IngestSyncService:
    settings = replace(Settings.from_environment(), robots_enabled=False)
    return IngestSyncService(
        repository,
        EventResearchPipeline(repository),
        client=RssFeedClient(httpx.AsyncClient(transport=transport)),
        guard=FetchGuard(settings=settings),
    )


def test_rss_sync_enters_pipeline_and_updates_source() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/feed.xml":
            return httpx.Response(200, headers={"etag": "new"}, content=RSS)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content="<p>公司发布业绩预告，预计净利润同比增长20%至30%。</p>".encode(),
        )

    repository = InMemoryRepository()
    source = Source(
        id="src_official",
        code="official",
        name="Official RSS",
        trust_tier="S",
        feed_url="https://source.example.com/feed.xml",
        allowed_domains=["source.example.com"],
    )
    repository.save_source(source)
    service = _sync_service(
        repository,
        httpx.MockTransport(handler),
    )

    result = asyncio.run(service.sync(source))

    assert result["processed"] == 1
    assert len(repository.documents) == 1
    assert repository.get_source(source.id).etag == "new"
    assert repository.get_source(source.id).consecutive_failures == 0


def test_rss_sync_records_backoff_after_fetch_failure() -> None:
    repository = InMemoryRepository()
    source = Source(
        id="src-failed",
        code="failed",
        name="Failed",
        trust_tier="S",
        feed_url="https://source.example.com/feed.xml",
        allowed_domains=["source.example.com"],
    )
    repository.save_source(source)
    service = _sync_service(
        repository,
        httpx.MockTransport(lambda _: httpx.Response(503)),
    )
    result = asyncio.run(service.sync(source))
    stored = repository.get_source(source.id)
    assert result["status"] == "degraded"
    assert stored.consecutive_failures == 1
    assert stored.next_retry_at is not None
    assert stored.last_error_code == "RSS_HTTP_503"
