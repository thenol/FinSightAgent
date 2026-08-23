from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.market.calendar import ReferenceTradingCalendar
from app.market.ingest import MarketIngestBatch, MarketIngestRun
from app.market.provider import MarketBar, MarketInstrument
from app.market.reference import MarketInstrumentCatalog
from app.market.replay import LocalArchiveMarketDataProvider
from app.market.replay_forecasts import HistoricalForecastReplayService
from app.market.storage import LocalMarketBatchStore
from app.platform.repository import InMemoryRepository

INSTRUMENT = MarketInstrument(
    id="cn:index:000300",
    market="cn",
    symbol="000300",
    name="沪深300",
    instrument_type="index",
)


def _write_history(root) -> None:
    trading_day = date(2026, 4, 1)
    last_day = date(2026, 8, 18)
    bars = []
    offset = 0
    while trading_day <= last_day:
        if trading_day.weekday() < 5:
            observed_at = datetime.combine(
                trading_day, time(15), tzinfo=ZoneInfo("Asia/Shanghai")
            ).astimezone(timezone.utc)
            knowledge_at = observed_at + timedelta(minutes=5)
            close = 100 + offset * 0.1
            bars.append(
                MarketBar(
                    instrument_id=INSTRUMENT.id,
                    market=INSTRUMENT.market,
                    symbol=INSTRUMENT.symbol,
                    interval="1d",
                    observed_at=observed_at,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    source="historical-test",
                    available_at=knowledge_at,
                    ingested_at=knowledge_at,
                )
            )
            offset += 1
        trading_day += timedelta(days=1)
    archived_at = datetime(2026, 8, 19, tzinfo=timezone.utc)
    run = MarketIngestRun(
        run_id="mkt_historical_replay",
        provider="historical-test",
        status="succeeded",
        started_at=archived_at,
        finished_at=archived_at,
        as_of=archived_at,
        interval="1d",
        instrument_count=1,
        bar_count=len(bars),
    )
    LocalMarketBatchStore(str(root)).write(MarketIngestBatch(run, tuple(bars)))


def test_historical_forecast_replay_is_point_in_time_and_idempotent(tmp_path) -> None:
    _write_history(tmp_path)
    repository = InMemoryRepository()
    service = HistoricalForecastReplayService(
        repository,
        LocalArchiveMarketDataProvider(str(tmp_path)),
        MarketInstrumentCatalog((INSTRUMENT,)),
        ReferenceTradingCalendar(),
    )
    arguments = {
        "instrument_ids": [INSTRUMENT.id],
        "forecast_from": date(2026, 8, 17),
        "forecast_to": date(2026, 8, 17),
        "horizon": 1,
        "lookback_days": 120,
        "publication_lag_minutes": 30,
        "max_slots": 10,
        "created_by": "usr_test",
        "evaluation_as_of": datetime(2026, 8, 19, tzinfo=timezone.utc),
    }

    first = service.run(**arguments)
    second = service.run(**arguments)

    assert first.source_provider == "local-market-archive"
    assert first.status == "completed"
    assert first.scheduled_slots == 1
    assert first.created_count == 1
    assert first.insufficient_count == 0
    assert first.settled_count == 1
    assert first.pending_outcome_count == 0
    assert second.created_count == 0
    assert second.reused_count == 1
    stored = repository.list_market_forecast_runs(limit=10)
    assert len(stored) == 1
    assert stored[0].input_snapshot["market_state"]["latest_observed_at"].startswith(
        "2026-08-17"
    )


def test_historical_forecast_replay_enforces_slot_limit(tmp_path) -> None:
    _write_history(tmp_path)
    service = HistoricalForecastReplayService(
        InMemoryRepository(),
        LocalArchiveMarketDataProvider(str(tmp_path)),
        MarketInstrumentCatalog((INSTRUMENT,)),
        ReferenceTradingCalendar(),
    )

    try:
        service.run(
            instrument_ids=[INSTRUMENT.id],
            forecast_from=date(2026, 8, 17),
            forecast_to=date(2026, 8, 18),
            horizon=1,
            lookback_days=120,
            publication_lag_minutes=30,
            max_slots=1,
            created_by="usr_test",
            evaluation_as_of=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )
    except ValueError as exc:
        assert str(exc) == "forecast replay exceeds max_slots"
    else:
        raise AssertionError("slot limit must reject an oversized replay")
