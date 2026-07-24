"""RSS Fetcher：包装 RssFeedClient，支持 RSSHub 路由与摘要正文。"""

from urllib.parse import urlparse

from app.domain import Source
from app.ingestion.fetchers.base import BaseFetcher
from app.ingestion.fetchers.schemas import FetchItem
from app.ingestion.guard import FetchGuard
from app.ingestion.html_text import choose_better_text, html_to_article_text
from app.ingestion.pdf import PdfBlockParser
from app.ingestion.rate_limiter import RateLimiter
from app.ingestion.rss import RssFeedClient, RssFetchError, RssSourceConfig
from app.platform.settings import Settings


class RSSFetcher(BaseFetcher):
    def __init__(
        self,
        source: Source,
        guard: FetchGuard,
        rate_limiter: RateLimiter,
        *,
        rss_client: RssFeedClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        super().__init__(source, guard, rate_limiter)
        self.client = rss_client or RssFeedClient()
        self.settings = settings or Settings.from_environment()

    def _resolve_feed_url(self) -> str | None:
        if self.source.feed_url:
            return self.source.feed_url
        route = self.extra_config.get("rsshub_route")
        if route:
            base = self.settings.rsshub_base_url.rstrip("/")
            return base + "/" + str(route).lstrip("/")
        return None

    def _rss_config(self, feed_url: str) -> RssSourceConfig:
        return RssSourceConfig(
            self.source.code,
            feed_url,
            tuple(self.source.allowed_domains),
        )

    async def fetch_list(self) -> list[FetchItem]:
        feed_url = self._resolve_feed_url()
        if not feed_url:
            return []
        if not await self._guarded_get(feed_url):
            raise RssFetchError("ROBOTS_DISALLOWED")
        config = self._rss_config(feed_url)
        fetched = await self.client.fetch_feed(
            config,
            etag=self.source.etag,
            last_modified=self.source.last_modified,
        )
        self._last_fetch_meta = {
            "etag": fetched.etag,
            "last_modified": fetched.last_modified,
            "not_modified": fetched.not_modified,
        }
        if fetched.not_modified:
            return []
        return [
            FetchItem(
                external_id=entry.external_id,
                title=entry.title,
                url=entry.url,
                published_raw=entry.published_raw,
                summary=entry.summary,
            )
            for entry in fetched.entries
        ]

    async def fetch_detail(self, item: FetchItem) -> FetchItem:
        # Default false: RSS summary only. Set extract_body=true for full pages.
        extract_body = bool(self.extra_config.get("extract_body", False))
        summary = (item.summary or "").strip()
        if not extract_body:
            content = choose_better_text(summary, item.title.strip())
            return FetchItem(
                external_id=item.external_id,
                title=item.title,
                url=item.url,
                published_raw=item.published_raw,
                summary=item.summary,
                content=content,
            )
        if not await self._guarded_get(item.url):
            raise RssFetchError("ROBOTS_DISALLOWED")
        config = self._rss_config(self._resolve_feed_url() or item.url)
        downloaded = await self.client.download_entry(config, item.url)
        page_text = self._content_text(downloaded.mime_type, downloaded.content)
        # 详情页常含脚本/导航噪音；质量不足时回退 RSS 摘要
        content = choose_better_text(page_text, summary, item.title.strip())
        return FetchItem(
            external_id=item.external_id,
            title=item.title,
            url=item.url,
            published_raw=item.published_raw,
            summary=item.summary,
            content=content,
        )

    def consume_fetch_meta(self) -> dict:
        return getattr(self, "_last_fetch_meta", {})

    @staticmethod
    def _content_text(mime_type: str, content: bytes) -> str:
        if mime_type == "application/pdf":
            return PdfBlockParser().parse(content).text
        return html_to_article_text(content.decode("utf-8", errors="replace"))

    @staticmethod
    def allowed_domains_for_feed(feed_url: str, extra: dict | None = None) -> list[str]:
        """从 feed URL 与 RSSHub 配置推导默认 allowed_domains。"""
        domains: list[str] = []
        parsed = urlparse(feed_url)
        if parsed.hostname:
            domains.append(parsed.hostname.lower())
        route = (extra or {}).get("rsshub_route")
        if route:
            base_host = urlparse(Settings.from_environment().rsshub_base_url).hostname
            if base_host:
                domains.append(base_host.lower())
        return sorted(set(domains))
