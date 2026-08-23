"""Replayable market-data collection contract.

The service intentionally stops at a normalized in-memory batch. Persistence
adapters can consume the batch without coupling providers to ClickHouse or
object storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.market.provider import MarketBar, MarketDataProvider
from app.platform.ids import new_id


@dataclass(frozen=True)
class MarketIngestRequest:
    instrument_ids: tuple[str, ...]
    start: datetime
    end: datetime
    interval: str
    as_of: datetime
    limit: int = 100_000

    def validate(self) -> None:
        if not self.instrument_ids:
            raise ValueError("market ingest requires instruments")
        if self.end < self.start:
            raise ValueError("market ingest range is invalid")
        if self.as_of.tzinfo is None or self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("market ingest timestamps require timezone")
        if self.interval not in {"5m", "1d"}:
            raise ValueError(f"unsupported market interval: {self.interval}")
        if self.limit < 1:
            raise ValueError("market ingest limit must be positive")


@dataclass(frozen=True)
class MarketIngestRun:
    run_id: str
    provider: str
    status: str
    started_at: datetime
    finished_at: datetime
    as_of: datetime
    interval: str
    instrument_count: int
    bar_count: int
    warnings: tuple[str, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class MarketIngestBatch:
    run: MarketIngestRun
    bars: tuple[MarketBar, ...]


class MarketIngestService:
    def __init__(self, provider: MarketDataProvider) -> None:
        self.provider = provider

    def collect(self, request: MarketIngestRequest) -> MarketIngestBatch:
        request.validate()
        started_at = datetime.now(timezone.utc)
        error_code = None
        warnings: list[str] = []
        bars: list[MarketBar] = []
        status = "failed"
        try:
            result = self.provider.get_bars(
                instrument_ids=list(request.instrument_ids),
                start=request.start,
                end=request.end,
                interval=request.interval,
                as_of=request.as_of,
                limit=request.limit,
            )
            bars = [
                bar
                for bar in result.bars
                if bar.available_at is None or bar.available_at <= request.as_of
            ]
            warnings.extend(result.warnings)
            if bars:
                status = "succeeded" if not warnings else "degraded"
            else:
                status = "degraded" if warnings or result.status != "ok" else "empty"
        except ValueError:
            raise
        except Exception:
            error_code = "MARKET_PROVIDER_EXCEPTION"
            warnings.append(error_code)
        finished_at = datetime.now(timezone.utc)
        run = MarketIngestRun(
            run_id=new_id("mkt"),
            provider=self.provider.capability.provider,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            as_of=request.as_of,
            interval=request.interval,
            instrument_count=len(request.instrument_ids),
            bar_count=len(bars),
            warnings=tuple(warnings),
            error_code=error_code,
        )
        return MarketIngestBatch(run=run, bars=tuple(bars))
