import asyncio
from dataclasses import replace

import httpx

from app.domain import Source
from app.ingestion.fetchers.rss import RSSFetcher
from app.ingestion.guard import FetchGuard
from app.ingestion.rate_limiter import RateLimiter
from app.ingestion.rss import RssFeedClient
from app.ingestion.seed_sources import SEED_SOURCES, build_seed_source, seed_sources
from app.platform.repository import InMemoryRepository
from app.platform.settings import Settings


def test_seed_sources_are_idempotent() -> None:
    repository = InMemoryRepository()
    first = seed_sources(repository)
    second = seed_sources(repository)
    assert first == len(SEED_SOURCES)
    assert second == 0
    assert len(repository.list_sources()) == len(SEED_SOURCES)


def test_build_seed_source_has_adapter_defaults() -> None:
    source = build_seed_source(SEED_SOURCES[0])
    assert source.adapter_type == "rss"
    assert source.trust_tier == "S"
    assert source.rate_limit_per_minute >= 1
    assert source.crawl_interval_seconds == 1800
    assert "stats.gov.cn" in source.allowed_domains


def test_rss_fetcher_resolves_rsshub_route() -> None:
    settings = replace(
        Settings.from_environment(),
        rsshub_base_url="http://127.0.0.1:1200",
        robots_enabled=False,
    )
    source = Source(
        id="src_rsshub",
        code="jin10",
        name="金十",
        trust_tier="A",
        feed_url="",
        allowed_domains=["127.0.0.1", "localhost"],
        extra_config={"rsshub_route": "/jin10"},
    )
    rss_xml = (
        b'<rss><channel><item><guid>j1</guid><title>test</title>'
        b'<link>http://127.0.0.1:1200/jin10/1</link></item></channel></rss>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jin10":
            return httpx.Response(200, content=rss_xml)
        return httpx.Response(404)

    fetcher = RSSFetcher(
        source,
        FetchGuard(settings=settings),
        RateLimiter(),
        rss_client=RssFeedClient(httpx.AsyncClient(transport=httpx.MockTransport(handler))),
        settings=settings,
    )
    items = asyncio.run(fetcher.fetch_list())
    assert len(items) == 1
    assert items[0].title == "test"
