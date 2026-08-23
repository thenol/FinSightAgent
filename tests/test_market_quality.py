from datetime import datetime, timezone

from app.market.provider import InMemoryMarketDataProvider
from app.market.quality import MarketQualityService


def test_market_quality_reports_missing_data_as_degraded() -> None:
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    result = MarketQualityService(InMemoryMarketDataProvider()).assess(
        instrument_ids=["cn:index:000300"], start=now, end=now,
        interval="1d", as_of=now, limit=10,
    )
    assert result.status == "degraded"
    assert result.missing_count == 1
    assert result.warnings == ("missing_instrument_data",)
