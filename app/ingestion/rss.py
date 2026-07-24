"""受限 RSS 采集和原文下载。

Source 配置由上层持久化；本模块只接受明确的 RSS URL 与允许域名，避免将
不可信 RSS 条目直接变成任意网络访问能力。
"""

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import feedparser
import httpx

from app.ingestion.html_text import html_to_article_text

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"application/pdf", "text/html", "application/xhtml+xml", "text/plain"}
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_USER_AGENT = "FinSightBot/0.1 (+https://github.com/finsight-agent; research)"
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


class RssFetchError(RuntimeError):
    pass


class UnsafeSourceUrl(ValueError):
    pass


@dataclass(frozen=True)
class RssSourceConfig:
    code: str
    feed_url: str
    allowed_domains: tuple[str, ...]


@dataclass(frozen=True)
class RssEntry:
    external_id: str
    title: str
    url: str
    published_raw: Optional[str]
    summary: str


@dataclass(frozen=True)
class RssFetchResult:
    entries: list[RssEntry]
    etag: Optional[str]
    last_modified: Optional[str]
    not_modified: bool = False


@dataclass(frozen=True)
class DownloadedDocument:
    url: str
    mime_type: str
    content: bytes


class RssFeedClient:
    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        *,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout_seconds, connect=15.0),
            headers=DEFAULT_HEADERS,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def fetch_feed(
        self,
        source: RssSourceConfig,
        *,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> RssFetchResult:
        self._validate_url(source.feed_url, source.allowed_domains)
        headers: dict[str, str] = {
            "Accept": DEFAULT_HEADERS["Accept"],
            "User-Agent": DEFAULT_USER_AGENT,
        }
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            response = await self.client.get(source.feed_url, headers=headers)
        except httpx.TimeoutException as exc:
            raise RssFetchError("RSS_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise RssFetchError(f"RSS_HTTP_ERROR:{type(exc).__name__}") from exc
        if response.status_code == 304:
            return RssFetchResult([], etag, last_modified, not_modified=True)
        if response.status_code != 200:
            raise RssFetchError(f"RSS_HTTP_{response.status_code}")
        self._ensure_size(response)
        parsed = feedparser.parse(response.content)
        if parsed.bozo and not parsed.entries:
            raise RssFetchError("RSS_PARSE_FAILED")
        entries: list[RssEntry] = []
        for item in parsed.entries:
            url = str(item.get("link", ""))
            if not url:
                continue
            try:
                self._validate_url(url, source.allowed_domains)
            except UnsafeSourceUrl:
                continue
            external_id = str(item.get("id") or item.get("guid") or url)
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            summary = self._html_to_text(str(item.get("summary", "")))
            entries.append(
                RssEntry(
                    external_id=external_id,
                    title=title,
                    url=url,
                    published_raw=str(item.get("published", "")) or None,
                    summary=summary,
                )
            )
        return RssFetchResult(
            entries=entries,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )

    async def download_entry(
        self,
        source: RssSourceConfig,
        url: str,
    ) -> DownloadedDocument:
        self._validate_url(url, source.allowed_domains)
        headers = {
            "Accept": "text/html, application/xhtml+xml, application/pdf, text/plain, */*",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        try:
            response = await self.client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise RssFetchError("DOCUMENT_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise RssFetchError(f"DOCUMENT_HTTP_ERROR:{type(exc).__name__}") from exc
        if response.status_code != 200:
            raise RssFetchError(f"DOCUMENT_HTTP_{response.status_code}")
        self._ensure_size(response)
        mime_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if mime_type not in ALLOWED_CONTENT_TYPES:
            raise RssFetchError("DOCUMENT_CONTENT_TYPE_REJECTED")
        return DownloadedDocument(
            url=str(response.url), mime_type=mime_type, content=response.content
        )

    def _validate_url(self, url: str, allowed_domains: tuple[str, ...]) -> None:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in {"https", "http"} or not hostname:
            raise UnsafeSourceUrl("HTTPS_URL_REQUIRED")
        if parsed.scheme == "http" and hostname not in _LOOPBACK_HOSTS:
            raise UnsafeSourceUrl("HTTPS_URL_REQUIRED")
        if not any(
            hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains
        ):
            raise UnsafeSourceUrl("SOURCE_DOMAIN_NOT_ALLOWED")

    def _ensure_size(self, response: httpx.Response) -> None:
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > MAX_RESPONSE_BYTES:
            raise RssFetchError("RESPONSE_TOO_LARGE")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise RssFetchError("RESPONSE_TOO_LARGE")

    def _html_to_text(self, value: str) -> str:
        return html_to_article_text(value)
