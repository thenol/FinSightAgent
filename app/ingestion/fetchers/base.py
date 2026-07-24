"""Fetcher 抽象基类（Strategy 模式，对齐 MarketMind）。"""

from abc import ABC, abstractmethod

from app.domain import Source
from app.ingestion.fetchers.schemas import FetchItem
from app.ingestion.guard import FetchGuard
from app.ingestion.rate_limiter import RateLimiter


class BaseFetcher(ABC):
    def __init__(self, source: Source, guard: FetchGuard, rate_limiter: RateLimiter) -> None:
        self.source = source
        self.guard = guard
        self.rate_limiter = rate_limiter
        self.extra_config = source.extra_config or {}

    async def _guarded_get(self, url: str) -> bool:
        if not await self.guard.can_fetch(url):
            return False
        rate = self.source.rate_limit_per_minute
        await self.rate_limiter.acquire(url, rate)
        return True

    @abstractmethod
    async def fetch_list(self) -> list[FetchItem]:
        ...

    @abstractmethod
    async def fetch_detail(self, item: FetchItem) -> FetchItem:
        ...
