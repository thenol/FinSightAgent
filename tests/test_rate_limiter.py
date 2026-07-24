import asyncio
import time

from app.ingestion.rate_limiter import RateLimiter


def test_rate_limiter_spaces_requests() -> None:
    limiter = RateLimiter()
    started = time.perf_counter()

    async def run() -> None:
        await limiter.acquire("https://example.com/a", 6000)
        await limiter.acquire("https://example.com/b", 6000)

    asyncio.run(run())
    elapsed = time.perf_counter() - started
    assert elapsed >= 0.01
