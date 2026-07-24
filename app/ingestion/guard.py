"""robots.txt 校验（抓取前强制调用，默认开启）。"""

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.platform.settings import Settings

_robots_cache: dict[str, RobotFileParser] = {}
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class FetchGuard:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or Settings.from_environment()
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(
            timeout=self.settings.fetch_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "FinSightBot"},
        )

    async def can_fetch(self, url: str, user_agent: str = "FinSightBot") -> bool:
        if not self.settings.robots_enabled:
            return True
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            return False
        host_root = f"{parsed.scheme}://{parsed.hostname}"
        parser = await self._load_parser(host_root)
        return parser.can_fetch(user_agent, url)

    async def _load_parser(self, host_root: str) -> RobotFileParser:
        if host_root in _robots_cache:
            return _robots_cache[host_root]
        parser = RobotFileParser()
        parser.set_url(f"{host_root}/robots.txt")
        client = await self._get_client()
        try:
            resp = await client.get(f"{host_root}/robots.txt")
            if resp.status_code in (401, 403):
                parser.disallow_all = True
            elif resp.status_code >= 400:
                parser.allow_all = True
            else:
                parser.parse(resp.text.splitlines())
        except Exception:
            parser.allow_all = True
        finally:
            if self._owns_client:
                await client.aclose()
        _robots_cache[host_root] = parser
        return parser
