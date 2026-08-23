"""Versioned market-data contracts used by retrieval and market research.

The contracts deliberately keep provider details out of the analysis layer.  A
provider may be unavailable or degraded; callers must inspect ``status`` and
``capability`` instead of treating an empty response as a valid market series.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

MARKET_CODES = ("cn", "hk", "us")
INTERVALS = ("5m", "1d")
# Providers report wall-clock timestamps in exchange-local time.  Localization
# is a contract concern, not an adapter detail: mislabeling exchange time as UTC
# silently shifts intraday bars and breaks as_of and freshness comparisons.
MARKET_TIMEZONES = {
    "cn": "Asia/Shanghai",
    "hk": "Asia/Hong_Kong",
    "us": "America/New_York",
}


@dataclass(frozen=True)
class MarketDataCapability:
    provider: str
    status: str
    supported_markets: list[str] = field(default_factory=list)
    supported_intervals: list[str] = field(default_factory=list)
    reason: str | None = None
    last_success_at: datetime | None = None


@dataclass(frozen=True)
class MarketInstrument:
    id: str
    market: str
    symbol: str
    name: str
    instrument_type: str
    exchange: str | None = None
    currency: str | None = None
    timezone: str = "UTC"
    sector_code: str | None = None
    sector_name: str | None = None
    active: bool = True
    valid_from: date | None = None
    valid_to: date | None = None
    provider_symbols: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TradingCalendarDay:
    market: str
    trading_day: date
    is_open: bool
    sessions: tuple[tuple[datetime, datetime], ...] = ()
    timezone: str = "UTC"
    source: str = "unknown"
    available_at: datetime | None = None


@dataclass(frozen=True)
class MarketBar:
    instrument_id: str
    market: str
    symbol: str
    interval: str
    observed_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    amount: float | None = None
    turnover: float | None = None
    vwap: float | None = None
    adjustment: str = "none"
    source: str = "unknown"
    available_at: datetime | None = None
    ingested_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.interval not in INTERVALS:
            raise ValueError(f"unsupported market interval: {self.interval}")
        if self.market not in MARKET_CODES:
            raise ValueError(f"unsupported market: {self.market}")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("market bar high/low is inconsistent with open/close")
        if self.close <= 0:
            raise ValueError("market bar close must be positive")


@dataclass(frozen=True)
class MarketSnapshot:
    instrument_id: str
    market: str
    symbol: str
    observed_at: datetime
    last: float
    change: float | None = None
    change_percent: float | None = None
    volume: float | None = None
    amount: float | None = None
    source: str = "unknown"
    available_at: datetime | None = None


@dataclass(frozen=True)
class MarketObservation:
    """Compatibility observation used by retrieval time-series queries."""

    security_id: str
    observed_at: datetime
    values: dict[str, float]
    source: str


@dataclass(frozen=True)
class MarketDataResult:
    status: str
    observations: list[MarketObservation] = field(default_factory=list)
    bars: list[MarketBar] = field(default_factory=list)
    snapshots: list[MarketSnapshot] = field(default_factory=list)
    instruments: list[MarketInstrument] = field(default_factory=list)
    calendar: list[TradingCalendarDay] = field(default_factory=list)
    capability: MarketDataCapability = field(
        default_factory=lambda: MarketDataCapability(
            provider="unknown", status="unavailable", reason="provider_not_configured"
        )
    )
    warnings: list[str] = field(default_factory=list)


class MarketDataProvider(Protocol):
    @property
    def capability(self) -> MarketDataCapability: ...

    def query(
        self,
        *,
        security_ids: list[str],
        start: datetime | None,
        end: datetime | None,
        as_of: datetime | None,
        limit: int,
    ) -> MarketDataResult: ...

    def get_bars(
        self,
        *,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
        interval: str,
        as_of: datetime,
        limit: int,
    ) -> MarketDataResult: ...

    def get_snapshots(
        self,
        *,
        instrument_ids: list[str],
        as_of: datetime,
    ) -> MarketDataResult: ...

    def get_calendar(
        self,
        *,
        market: str,
        start: date,
        end: date,
        as_of: datetime,
    ) -> MarketDataResult: ...


class UnavailableMarketDataProvider:
    """Explicit capability implementation when no source is configured."""

    capability = MarketDataCapability(
        provider="none",
        status="unavailable",
        reason="market_data_provider_not_configured",
    )

    def query(self, **kwargs: Any) -> MarketDataResult:
        _ = kwargs
        return MarketDataResult(status="unavailable", capability=self.capability)

    def get_bars(self, **kwargs: Any) -> MarketDataResult:
        _ = kwargs
        return MarketDataResult(status="unavailable", capability=self.capability)

    def get_snapshots(self, **kwargs: Any) -> MarketDataResult:
        _ = kwargs
        return MarketDataResult(status="unavailable", capability=self.capability)

    def get_calendar(self, **kwargs: Any) -> MarketDataResult:
        _ = kwargs
        return MarketDataResult(status="unavailable", capability=self.capability)


class InMemoryMarketDataProvider:
    """Deterministic provider for contract tests and local replay."""

    def __init__(
        self,
        bars: list[MarketBar] | None = None,
        snapshots: list[MarketSnapshot] | None = None,
        calendar: list[TradingCalendarDay] | None = None,
    ) -> None:
        self._bars = tuple(bars or ())
        self._snapshots = tuple(snapshots or ())
        self._calendar = tuple(calendar or ())
        self._capability = MarketDataCapability(
            provider="in-memory",
            status="available",
            supported_markets=sorted({item.market for item in self._bars}),
            supported_intervals=sorted({item.interval for item in self._bars}),
        )

    @property
    def capability(self) -> MarketDataCapability:
        return self._capability

    def _visible(self, item: Any, as_of: datetime) -> bool:
        available_at = getattr(item, "available_at", None)
        return available_at is None or available_at <= as_of

    def get_bars(self, *, instrument_ids, start, end, interval, as_of, limit) -> MarketDataResult:
        if as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        selected = [
            item for item in self._bars
            if item.instrument_id in instrument_ids
            and start <= item.observed_at <= end
            and item.interval == interval
        ]
        leaked = [item for item in selected if not self._visible(item, as_of)]
        if leaked:
            raise ValueError("market data is newer than as_of")
        return MarketDataResult(status="ok", bars=selected[:limit], capability=self.capability)

    def get_snapshots(self, *, instrument_ids, as_of) -> MarketDataResult:
        selected = [item for item in self._snapshots if item.instrument_id in instrument_ids]
        leaked = [item for item in selected if not self._visible(item, as_of)]
        if leaked:
            raise ValueError("market snapshot is newer than as_of")
        return MarketDataResult(status="ok", snapshots=selected, capability=self.capability)

    def get_calendar(self, *, market, start, end, as_of) -> MarketDataResult:
        selected = [
            item
            for item in self._calendar
            if item.market == market and start <= item.trading_day <= end
        ]
        leaked = [item for item in selected if not self._visible(item, as_of)]
        if leaked:
            raise ValueError("trading calendar is newer than as_of")
        return MarketDataResult(status="ok", calendar=selected, capability=self.capability)

    def query(self, *, security_ids, start, end, as_of, limit) -> MarketDataResult:
        if start is None or end is None or as_of is None:
            return MarketDataResult(
                status="invalid",
                capability=self.capability,
                warnings=["start_end_as_of_required"],
            )
        result = self.get_bars(
            instrument_ids=security_ids,
            start=start,
            end=end,
            interval="1d",
            as_of=as_of,
            limit=limit,
        )
        observations = [
            MarketObservation(
                item.instrument_id,
                item.observed_at,
                {"close": item.close},
                item.source,
            )
            for item in result.bars
        ]
        return MarketDataResult(**{**result.__dict__, "observations": observations})
