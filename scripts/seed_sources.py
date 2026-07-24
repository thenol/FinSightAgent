#!/usr/bin/env python3
"""插入 MarketMind 风格 RSS 种子源。"""

from app.ingestion.seed_sources import SEED_SOURCES, seed_sources
from app.platform.repository import InMemoryRepository, SqlAlchemyRepository
from app.platform.settings import Settings


def main() -> None:
    settings = Settings.from_environment()
    if settings.repository == "postgresql":
        repository = SqlAlchemyRepository(settings.database_url)
    else:
        repository = InMemoryRepository()
    count = seed_sources(repository, skip_existing=True)
    print(f"seeded {count} new sources (target {len(SEED_SOURCES)})")


if __name__ == "__main__":
    main()
