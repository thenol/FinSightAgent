"""MarketMind 实测 RSS 种子源（CN 优先 + 统计局 S 级）。"""

from dataclasses import dataclass

from app.domain import Source
from app.ingestion.fetchers.rss import RSSFetcher
from app.platform.ids import new_id


@dataclass(frozen=True)
class SeedSource:
    code: str
    name: str
    trust_tier: str
    feed_url: str
    allowed_domains: list[str]
    rate_limit_per_minute: int = 10
    crawl_interval_seconds: int = 3600
    extra_config: dict | None = None


def _default_interval(trust_tier: str) -> int:
    return 1800 if trust_tier == "S" else 3600


SEED_SOURCES: list[SeedSource] = [
    SeedSource(
        code="stats-gov-zxfb",
        name="国家统计局-最新发布",
        trust_tier="S",
        feed_url="https://www.stats.gov.cn/sj/zxfb/rss.xml",
        allowed_domains=["stats.gov.cn", "www.stats.gov.cn"],
        rate_limit_per_minute=30,
        crawl_interval_seconds=1800,
        extra_config={"extract_body": False, "max_items_per_sync": 15},
    ),
    SeedSource(
        code="wallstreetcn",
        name="华尔街见闻",
        trust_tier="A",
        feed_url="https://dedicated.wallstreetcn.com/rss.xml",
        allowed_domains=["wallstreetcn.com", "dedicated.wallstreetcn.com"],
        rate_limit_per_minute=30,
        crawl_interval_seconds=3600,
        extra_config={"extract_body": False, "max_items_per_sync": 20},
    ),
    SeedSource(
        code="eastmoney-rss",
        name="东方财富",
        trust_tier="A",
        feed_url="https://rss.eastmoney.com/rss_partener.xml",
        allowed_domains=["eastmoney.com", "rss.eastmoney.com"],
        rate_limit_per_minute=30,
        crawl_interval_seconds=3600,
        extra_config={"extract_body": False, "max_items_per_sync": 20},
    ),
    SeedSource(
        code="chinanews-finance",
        name="中新网财经",
        trust_tier="A",
        feed_url="https://www.chinanews.com.cn/rss/finance.xml",
        allowed_domains=["chinanews.com.cn", "www.chinanews.com.cn"],
        rate_limit_per_minute=30,
        crawl_interval_seconds=3600,
        extra_config={"extract_body": False, "max_items_per_sync": 20},
    ),
]


def build_seed_source(seed: SeedSource) -> Source:
    domains = seed.allowed_domains or RSSFetcher.allowed_domains_for_feed(
        seed.feed_url, seed.extra_config
    )
    return Source(
        id=new_id("src"),
        code=seed.code,
        name=seed.name,
        trust_tier=seed.trust_tier,
        feed_url=seed.feed_url,
        allowed_domains=domains,
        adapter_type="rss",
        rate_limit_per_minute=seed.rate_limit_per_minute,
        crawl_interval_seconds=seed.crawl_interval_seconds or _default_interval(seed.trust_tier),
        extra_config=seed.extra_config or {},
    )


def seed_sources(repository, *, skip_existing: bool = True) -> int:
    """插入种子源；按 code 去重。返回新插入数量。"""
    inserted = 0
    for seed in SEED_SOURCES:
        if skip_existing and repository.get_source_by_code(seed.code):
            continue
        repository.save_source(build_seed_source(seed))
        inserted += 1
    return inserted
