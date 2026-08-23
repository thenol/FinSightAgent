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


def test_market_worker_reads_persisted_master_data_catalog(monkeypatch) -> None:
    from app.market.master_data import seed_market_master_data
    from app.market.provider import MarketInstrument
    from app.platform.repository import InMemoryRepository
    from app.platform.settings import Settings
    from app.worker import load_market_catalog, market_instrument_ids

    repository = InMemoryRepository()
    seed_market_master_data(repository)
    extra = MarketInstrument(
        id="cn:etf:custom",
        market="cn",
        symbol="510050",
        name="自定义ETF",
        instrument_type="etf",
        provider_symbols={"eastmoney": "1.510050"},
    )
    repository.save_market_instrument(extra)
    settings = Settings(
        environment="test",
        repository="memory",
        database_url="",
        redis_url="",
        artifact_root=".data/artifacts",
        jwt_secret="test-secret-32-bytes-long!!",
        bootstrap_admin_username="",
        bootstrap_admin_password="",
    )
    catalog = load_market_catalog(settings, repository)
    monkeypatch.delenv("MARKET_DATA_INSTRUMENT_IDS", raising=False)

    ids = market_instrument_ids(catalog)
    assert extra.id in ids
    monkeypatch.setenv("MARKET_DATA_INSTRUMENT_IDS", "cn:index:missing")
    try:
        market_instrument_ids(catalog)
        raise AssertionError("missing instrument should be rejected")
    except RuntimeError as exc:
        assert "cn:index:missing" in str(exc)
