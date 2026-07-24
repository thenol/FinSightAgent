"""Fetcher 工厂：按 adapter_type 分发。"""

from app.domain import Source
from app.ingestion.fetchers.base import BaseFetcher
from app.ingestion.fetchers.rss import RSSFetcher
from app.ingestion.guard import FetchGuard
from app.ingestion.rate_limiter import RateLimiter
from app.ingestion.rss import RssFeedClient

_FETCHER_REGISTRY: dict[str, type[BaseFetcher]] = {
    "rss": RSSFetcher,
}


def register_fetcher(adapter_type: str, fetcher_cls: type[BaseFetcher]) -> None:
    _FETCHER_REGISTRY[adapter_type] = fetcher_cls


def get_fetcher(
    source: Source,
    *,
    guard: FetchGuard | None = None,
    rate_limiter: RateLimiter | None = None,
    rss_client: RssFeedClient | None = None,
) -> BaseFetcher:
    cls = _FETCHER_REGISTRY.get(source.adapter_type or "rss")
    if cls is None:
        raise ValueError(f"unsupported adapter_type: {source.adapter_type!r}")
    guard = guard or FetchGuard()
    rate_limiter = rate_limiter or RateLimiter()
    if cls is RSSFetcher:
        return RSSFetcher(source, guard, rate_limiter, rss_client=rss_client)
    return cls(source, guard, rate_limiter)
