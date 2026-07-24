"""ingestion fetchers package."""

from app.ingestion.fetchers.base import BaseFetcher
from app.ingestion.fetchers.factory import get_fetcher, register_fetcher
from app.ingestion.fetchers.rss import RSSFetcher
from app.ingestion.fetchers.schemas import FetchItem

__all__ = ["BaseFetcher", "FetchItem", "RSSFetcher", "get_fetcher", "register_fetcher"]
