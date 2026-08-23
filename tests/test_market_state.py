from datetime import datetime, timedelta, timezone

from app.market.provider import InMemoryMarketDataProvider, MarketBar
from app.market.state import MarketStateService


def test_market_state_calculates_trend_and_volatility_from_replayable_bars() -> None:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    bars = [
        MarketBar(
            instrument_id="cn:index:000300",
            market="cn",
            symbol="000300",
            interval="1d",
            observed_at=start + timedelta(days=index),
            open=100 + index,
            high=101 + index,
            low=99 + index,
            close=100 + index,
            source="test",
            available_at=start + timedelta(days=40),
        )
        for index in range(25)
    ]
    # The bars are available only after the historical replay timestamp.
    as_of = start + timedelta(days=40)
    states = MarketStateService(InMemoryMarketDataProvider(bars)).calculate(
        instrument_ids=["cn:index:000300"],
        start=start,
        end=start + timedelta(days=24),
        as_of=as_of,
    )
    state = states[0]
    assert state.trend == "uptrend"
    assert state.data_status == "ok"
    assert state.coverage == 1.0
    assert state.realized_volatility is not None


def test_market_state_is_explicit_when_data_is_missing() -> None:
    as_of = datetime(2026, 8, 18, tzinfo=timezone.utc)
    states = MarketStateService(InMemoryMarketDataProvider()).calculate(
        instrument_ids=["cn:index:000300"],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, tzinfo=timezone.utc),
        as_of=as_of,
    )
    assert states[0].data_status == "insufficient_data"
    assert states[0].trend == "unknown"
    assert states[0].coverage == 0.0


def test_market_state_marks_old_bars_as_stale() -> None:
    observed = datetime(2026, 8, 1, tzinfo=timezone.utc)
    bar = MarketBar(
        instrument_id="cn:index:000300",
        market="cn",
        symbol="000300",
        interval="1d",
        observed_at=observed,
        open=100,
        high=101,
        low=99,
        close=100,
        source="test",
        available_at=observed,
    )
    states = MarketStateService(InMemoryMarketDataProvider([bar])).calculate(
        instrument_ids=["cn:index:000300"],
        start=observed,
        end=datetime(2026, 8, 18, tzinfo=timezone.utc),
        as_of=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    assert states[0].data_status == "stale_data"
    assert states[0].freshness_lag_seconds == 17 * 86400


def test_market_state_does_not_treat_weekend_as_missing_sessions() -> None:
    observed = datetime(2026, 8, 21, tzinfo=timezone.utc)  # Friday
    bar = MarketBar(
        instrument_id="cn:index:000300", market="cn", symbol="000300", interval="1d",
        observed_at=observed, open=100, high=101, low=99, close=100,
        source="test", available_at=observed,
    )

    state = MarketStateService(InMemoryMarketDataProvider([bar])).calculate(
        instrument_ids=["cn:index:000300"], start=observed,
        end=datetime(2026, 8, 23, tzinfo=timezone.utc),
        as_of=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )[0]

    assert state.data_status == "ok"
    assert state.expected_observation_count == 1
