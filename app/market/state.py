"""Deterministic market-state features for outlook models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean, pstdev

from app.market.provider import MarketBar, MarketDataProvider


@dataclass(frozen=True)
class MarketStateSnapshot:
    instrument_id: str
    as_of: datetime
    latest_observed_at: datetime | None
    observation_count: int
    latest_return: float | None
    trend_score: float | None
    realized_volatility: float | None
    trend: str
    volatility: str
    data_status: str
    coverage: float
    expected_observation_count: int = 0
    freshness_lag_seconds: float | None = None
    data_warnings: tuple[str, ...] = ()
    latest_close: float | None = None


class MarketStateService:
    def __init__(self, provider: MarketDataProvider, calendar=None) -> None:
        self.provider = provider
        self.calendar = calendar

    def calculate(
        self,
        *,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
        as_of: datetime,
        interval: str = "1d",
        limit: int = 250,
    ) -> list[MarketStateSnapshot]:
        if as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        result = self.provider.get_bars(
            instrument_ids=instrument_ids,
            start=start,
            end=end,
            interval=interval,
            as_of=as_of,
            limit=limit,
        )
        by_instrument: dict[str, list] = {instrument_id: [] for instrument_id in instrument_ids}
        for bar in result.bars:
            by_instrument.setdefault(bar.instrument_id, []).append(bar)
        snapshots = []
        for instrument_id in instrument_ids:
            bars = sorted(by_instrument.get(instrument_id, []), key=lambda item: item.observed_at)
            market = bars[0].market if bars else instrument_id.split(":", 1)[0]
            expected = max(1, min(limit, self._open_day_count(market, start.date(), end.date())))
            closes = [bar.close for bar in bars if bar.close > 0]
            returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
            trend_score = None
            realized_volatility = None
            if len(closes) >= 3:
                short_window = closes[-min(5, len(closes)):]
                long_window = closes[-min(20, len(closes)):]
                trend_score = short_window[-1] / mean(long_window) - 1
            if len(returns) >= 2:
                realized_volatility = pstdev(returns) * math.sqrt(252)
            snapshots.append(
                _build_snapshot(
                    instrument_id=instrument_id,
                    as_of=as_of,
                    end=end,
                    interval=interval,
                    bars=bars,
                    returns=returns,
                    trend_score=trend_score,
                    realized_volatility=realized_volatility,
                    expected=expected,
                    market=market,
                    calendar=self.calendar,
                    data_warnings=tuple(
                        warning for warning in result.warnings
                        if warning.startswith(f"{instrument_id}:")
                    ),
                )
            )
        return snapshots

    def _open_day_count(self, market: str, start: date, end: date) -> int:
        if self.calendar is not None and hasattr(self.calendar, "count_open_days"):
            return self.calendar.count_open_days(market, start, end)
        return _weekday_count_dates(start, end)


def _build_snapshot(
    *, instrument_id: str, as_of: datetime, end: datetime, interval: str,
    bars: list[MarketBar], returns: list[float], trend_score: float | None,
    realized_volatility: float | None, expected: int, market: str, calendar,
    data_warnings: tuple[str, ...],
) -> MarketStateSnapshot:
    freshness_lag = None
    if bars:
        reference_time = min(end, as_of)
        freshness_lag = max(0.0, (reference_time - bars[-1].observed_at).total_seconds())
    if interval == "5m":
        stale = freshness_lag is not None and freshness_lag > 30 * 60
    elif bars:
        reference = min(end, as_of)
        open_days = (
            calendar.count_open_days(market, bars[-1].observed_at.date(), reference.date())
            if calendar is not None and hasattr(calendar, "count_open_days")
            else _weekday_count_dates(bars[-1].observed_at.date(), reference.date())
        )
        stale = max(0, open_days - 1) > 1
    else:
        stale = False
    if not bars:
        data_status = "insufficient_data"
    elif stale:
        data_status = "stale_data"
    else:
        data_status = "ok"
    return MarketStateSnapshot(
        instrument_id=instrument_id,
        as_of=as_of,
        latest_observed_at=bars[-1].observed_at if bars else None,
        observation_count=len(bars),
        latest_return=returns[-1] if returns else None,
        trend_score=trend_score,
        realized_volatility=realized_volatility,
        trend=_trend_label(trend_score),
        volatility=_volatility_label(realized_volatility),
        data_status=data_status,
        coverage=min(1.0, len(bars) / expected),
        expected_observation_count=expected,
        freshness_lag_seconds=freshness_lag,
        data_warnings=data_warnings,
        latest_close=bars[-1].close if bars else None,
    )


def _weekday_count(start: datetime, end: datetime) -> int:
    """Count weekday observations in a closed interval.

    This is an explicit temporary approximation until the exchange-calendar
    adapter replaces it.  It avoids treating weekends as missing sessions.
    """
    if end < start:
        return 0
    return _weekday_count_dates(start.date(), end.date())


def _weekday_count_dates(start: date, end: date) -> int:
    cursor = start
    finish = end
    count = 0
    while cursor <= finish:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def _trend_label(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 0.01:
        return "uptrend"
    if score <= -0.01:
        return "downtrend"
    return "range"


def _volatility_label(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value >= 0.35:
        return "high"
    if value <= 0.18:
        return "low"
    return "normal"
