"""Leakage-safe batch issuance of forecasts from immutable market archives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.market.forecasting import ForecastLifecycleService
from app.market.provider import MarketDataProvider
from app.market.reference import MarketInstrumentCatalog
from app.platform.repository import Repository


@dataclass(frozen=True)
class HistoricalForecastReplayReceipt:
    forecast_from: date
    forecast_to: date
    scheduled_slots: int
    processed_slots: int
    created_count: int
    reused_count: int
    insufficient_count: int
    settled_count: int
    pending_outcome_count: int
    excluded_outcome_count: int
    evaluation_as_of: datetime | None
    run_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    status: str
    source_provider: str
    rule_version: str = "historical-forecast-replay-v1"


class HistoricalForecastReplayService:
    def __init__(
        self,
        repository: Repository,
        provider: MarketDataProvider,
        catalog: MarketInstrumentCatalog,
        calendar,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.catalog = catalog
        self.calendar = calendar

    def run(
        self,
        *,
        instrument_ids: list[str],
        forecast_from: date,
        forecast_to: date,
        horizon: int,
        lookback_days: int,
        publication_lag_minutes: int,
        max_slots: int,
        created_by: str,
        settle_outcomes: bool = True,
        evaluation_as_of: datetime | None = None,
    ) -> HistoricalForecastReplayReceipt:
        if forecast_to < forecast_from:
            raise ValueError("forecast replay range is invalid")
        if lookback_days < 30 or max_slots < 1 or publication_lag_minutes < 0:
            raise ValueError("forecast replay parameters are invalid")
        instruments = [self.catalog.get(item) for item in instrument_ids]
        if any(item is None for item in instruments):
            raise ValueError("forecast replay instrument is unknown")
        by_market: dict[str, list[str]] = {}
        for instrument in instruments:
            assert instrument is not None
            by_market.setdefault(instrument.market, []).append(instrument.id)
        scheduled: list[tuple[datetime, list[str]]] = []
        now = datetime.now(timezone.utc)
        effective_evaluation_as_of = evaluation_as_of or now
        if effective_evaluation_as_of.tzinfo is None:
            raise ValueError("forecast replay evaluation_as_of requires timezone")
        if effective_evaluation_as_of > now:
            raise ValueError("forecast replay cannot evaluate with a future cutoff")
        warnings: list[str] = []
        for market, market_instruments in sorted(by_market.items()):
            result = self.calendar.query(
                market=market,
                start=forecast_from,
                end=forecast_to,
                as_of=now,
            )
            warnings.extend(result.warnings)
            for item in result.calendar:
                if not item.is_open or not item.sessions:
                    continue
                decision_at = item.sessions[-1][1] + timedelta(
                    minutes=publication_lag_minutes
                )
                if decision_at.astimezone(timezone.utc) <= now:
                    scheduled.append((decision_at, market_instruments))
        scheduled.sort(key=lambda item: (item[0], item[1]))
        scheduled_slots = sum(len(items) for _, items in scheduled)
        if scheduled_slots > max_slots:
            raise ValueError("forecast replay exceeds max_slots")
        lifecycle = ForecastLifecycleService(
            self.repository, self.provider, self.catalog, self.calendar
        )
        created_count = 0
        reused_count = 0
        insufficient_count = 0
        run_ids: list[str] = []
        for as_of, market_instruments in scheduled:
            receipt = lifecycle.issue(
                instrument_ids=market_instruments,
                start=as_of - timedelta(days=lookback_days),
                end=as_of,
                horizon=horizon,
                interval="1d",
                as_of=as_of,
                limit=5000,
                created_by=created_by,
            )
            created_count += receipt.created_count
            reused_count += receipt.reused_count
            insufficient_count += sum(
                item.forecast_status == "insufficient_data" for item in receipt.runs
            )
            run_ids.extend(item.id for item in receipt.runs)
        settled_count = 0
        pending_outcome_count = 0
        excluded_outcome_count = 0
        if settle_outcomes and run_ids:
            settlement = lifecycle.settle(
                evaluation_as_of=effective_evaluation_as_of,
                forecast_ids=list(dict.fromkeys(run_ids)),
            )
            settled_count = settlement.settled_count
            pending_outcome_count = settlement.pending_count
            excluded_outcome_count = settlement.excluded_count
            warnings.extend(settlement.warnings)
        status = "completed"
        if not scheduled:
            status = "empty"
        elif insufficient_count == len(run_ids):
            status = "insufficient_data"
        elif insufficient_count:
            status = "degraded"
        return HistoricalForecastReplayReceipt(
            forecast_from=forecast_from,
            forecast_to=forecast_to,
            scheduled_slots=scheduled_slots,
            processed_slots=len(run_ids),
            created_count=created_count,
            reused_count=reused_count,
            insufficient_count=insufficient_count,
            settled_count=settled_count,
            pending_outcome_count=pending_outcome_count,
            excluded_outcome_count=excluded_outcome_count,
            evaluation_as_of=(effective_evaluation_as_of if settle_outcomes else None),
            run_ids=tuple(run_ids),
            warnings=tuple(dict.fromkeys(warnings)),
            status=status,
            source_provider=self.provider.capability.provider,
        )
