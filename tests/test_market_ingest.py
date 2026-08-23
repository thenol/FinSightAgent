from datetime import datetime, timedelta, timezone

import pytest

from app.market.ingest import MarketIngestRequest, MarketIngestService
from app.market.provider import InMemoryMarketDataProvider, MarketBar


def test_ingest_collects_replayable_batch_and_run_metadata() -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    bars = [
        MarketBar(
            instrument_id="cn:index:000300", market="cn", symbol="000300", interval="1d",
            observed_at=start, open=100, high=101, low=99, close=100,
            source="test", available_at=start + timedelta(days=1),
        )
    ]
    batch = MarketIngestService(InMemoryMarketDataProvider(bars)).collect(
        MarketIngestRequest(
            instrument_ids=("cn:index:000300",), start=start, end=start,
            interval="1d", as_of=start + timedelta(days=2),
        )
    )
    assert batch.run.status == "succeeded"
    assert batch.run.bar_count == 1
    assert batch.bars[0].instrument_id == "cn:index:000300"
    assert batch.run.finished_at >= batch.run.started_at


def test_ingest_rejects_future_available_bars_at_as_of() -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    bar = MarketBar(
        instrument_id="cn:index:000300", market="cn", symbol="000300", interval="1d",
        observed_at=now, open=100, high=101, low=99, close=100,
        source="test", available_at=now + timedelta(days=1),
    )
    with pytest.raises(ValueError, match="newer than as_of"):
        MarketIngestService(InMemoryMarketDataProvider([bar])).collect(
            MarketIngestRequest(
                instrument_ids=("cn:index:000300",), start=now, end=now,
                interval="1d", as_of=now,
            )
        )


def test_ingest_requires_timezone_and_valid_range() -> None:
    provider = InMemoryMarketDataProvider()
    request = MarketIngestRequest(
        instrument_ids=("cn:index:000300",),
        start=datetime(2026, 8, 2), end=datetime(2026, 8, 1),
        interval="1d", as_of=datetime(2026, 8, 2),
    )
    with pytest.raises(ValueError, match="range is invalid"):
        MarketIngestService(provider).collect(request)
