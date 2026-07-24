"""域名级限速（进程内时间窗，无 Redis 依赖）。"""

import asyncio
import time
from urllib.parse import urlparse

_local_last: dict[str, float] = {}
_token_locks: dict[str, asyncio.Lock] = {}


class RateLimiter:
    async def acquire(self, url: str, rate_per_minute: int, timeout: float = 30.0) -> None:
        if rate_per_minute <= 0:
            return
        host = urlparse(url).netloc or url
        lock = _token_locks.setdefault(host, asyncio.Lock())
        async with lock:
            interval = 60.0 / rate_per_minute
            key = f"_last:{host}"
            now = time.time()
            last = _local_last.get(key, 0.0)
            wait = interval - (now - last)
            if wait > 0:
                if wait > timeout:
                    raise TimeoutError(f"rate limit wait {wait:.1f}s exceeds timeout for {host}")
                await asyncio.sleep(wait)
            _local_last[key] = time.time()
