"""Quality summary for a market-data query before it enters research models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.market.provider import MarketDataProvider
from app.market.state import MarketStateService


@dataclass(frozen=True)
class MarketQualitySummary:
    as_of: datetime
    interval: str
    instrument_count: int
    ok_count: int
    stale_count: int
    missing_count: int
    average_coverage: float
    max_freshness_lag_seconds: float | None
    status: str
    warnings: tuple[str, ...]


class MarketQualityService:
    def __init__(self, provider: MarketDataProvider, calendar=None) -> None:
        self.provider = provider
        self.calendar = calendar

    def assess(self, *, instrument_ids: list[str], start: datetime, end: datetime,
               interval: str, as_of: datetime, limit: int) -> MarketQualitySummary:
        states = MarketStateService(self.provider, self.calendar).calculate(
            instrument_ids=instrument_ids, start=start, end=end,
            interval=interval, as_of=as_of, limit=limit,
        )
        stale_count = sum(item.data_status == "stale_data" for item in states)
        missing_count = sum(item.data_status == "insufficient_data" for item in states)
        ok_count = sum(item.data_status == "ok" for item in states)
        coverages = [item.coverage for item in states]
        lags = [
            item.freshness_lag_seconds
            for item in states
            if item.freshness_lag_seconds is not None
        ]
        warnings: list[str] = []
        if stale_count:
            warnings.append("stale_data_detected")
        if missing_count:
            warnings.append("missing_instrument_data")
        status = "unavailable" if not states else "degraded" if ok_count != len(states) else "ok"
        return MarketQualitySummary(
            as_of=as_of, interval=interval, instrument_count=len(states), ok_count=ok_count,
            stale_count=stale_count, missing_count=missing_count,
            average_coverage=round(sum(coverages) / len(coverages), 4) if coverages else 0.0,
            max_freshness_lag_seconds=max(lags) if lags else None,
            status=status, warnings=tuple(warnings),
        )
