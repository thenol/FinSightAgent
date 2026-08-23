from datetime import datetime, timezone

from app.market.provider import InMemoryMarketDataProvider, MarketBar
from app.market.storage import LocalMarketBatchStore
from app.market.worker import MarketDataWorker


def test_market_worker_collects_and_persists_one_run(tmp_path) -> None:
    now = datetime(2026, 8, 19, 15, tzinfo=timezone.utc)
    provider = InMemoryMarketDataProvider([
        MarketBar(
            instrument_id="cn:index:000300", market="cn", symbol="000300", interval="1d",
            observed_at=now, open=100, high=101, low=99, close=100,
            source="test", available_at=now,
        )
    ])
    result = MarketDataWorker(
        provider, LocalMarketBatchStore(str(tmp_path)), instrument_ids=("cn:index:000300",)
    ).run_once(now=now)
    assert result.batch.run.status == "succeeded"
    assert result.receipt.bar_count == 1
