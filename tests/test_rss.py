import asyncio

import httpx
import pytest

from app.ingestion.rss import RssFeedClient, RssSourceConfig, UnsafeSourceUrl

RSS = (
    b"<?xml version='1.0'?><rss version='2.0'><channel><title>Test</title>"
    b"<item><guid>notice-1</guid><title>Official notice</title>"
    b"<link>https://disclosure.example.com/n/1</link>"
    b"<description>&lt;p&gt;Summary text&lt;/p&gt;</description></item>"
    b"</channel></rss>"
)


def client(handler):
    return RssFeedClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def source() -> RssSourceConfig:
    return RssSourceConfig(
        code="official-rss",
        feed_url="https://disclosure.example.com/feed.xml",
        allowed_domains=("disclosure.example.com",),
    )


def test_rss_fetch_uses_conditional_headers_and_parses_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"old"'
        return httpx.Response(
            200,
            headers={"etag": '"new"', "last-modified": "Sat, 12 Jul 2026 00:00:00 GMT"},
            content=RSS,
        )

    result = asyncio.run(client(handler).fetch_feed(source(), etag='"old"'))

    assert result.not_modified is False
    assert result.etag == '"new"'
    assert result.entries[0].external_id == "notice-1"
    assert result.entries[0].summary == "Summary text"


def test_rss_304_returns_no_entries() -> None:
    result = asyncio.run(
        client(lambda request: httpx.Response(304)).fetch_feed(source(), etag='"current"')
    )

    assert result.not_modified is True
    assert result.entries == []


def test_entry_download_rejects_non_allowlisted_redirect_target() -> None:
    async def run() -> None:
        with pytest.raises(UnsafeSourceUrl, match="SOURCE_DOMAIN_NOT_ALLOWED"):
            await client(lambda request: httpx.Response(200)).download_entry(
                source(),
                "https://untrusted.example.net/notice",
            )

    asyncio.run(run())


def test_entry_download_returns_allowed_html() -> None:
    result = asyncio.run(
        client(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<p>Official disclosure</p>",
            )
        ).download_entry(source(), "https://disclosure.example.com/n/1")
    )

    assert result.mime_type == "text/html"
    assert result.content == b"<p>Official disclosure</p>"
