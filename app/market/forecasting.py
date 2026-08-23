"""Forecast issuance and outcome-settlement lifecycle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain import MarketCalibrationVersion, MarketForecastOutcome, MarketForecastRun
from app.market.evaluation import label_return
from app.market.factors import EventImpactFactorService
from app.market.outlook import MarketOutlookService
from app.market.provider import MarketDataProvider
from app.market.reference import MarketInstrumentCatalog
from app.market.state import MarketStateService
from app.platform.ids import new_id
from app.platform.repository import Repository

FORECAST_FACTOR_RULE_VERSION = "forecast-factor-v1"


@dataclass(frozen=True)
class ForecastIssueReceipt:
    runs: tuple[MarketForecastRun, ...]
    created_count: int
    reused_count: int


@dataclass(frozen=True)
class ForecastSettlementReceipt:
    evaluated_as_of: datetime
    considered_count: int
    settled_count: int
    pending_count: int
    excluded_count: int
    outcomes: tuple[MarketForecastOutcome, ...]
    warnings: tuple[str, ...]


class ForecastLifecycleService:
    def __init__(
        self,
        repository: Repository,
        provider: MarketDataProvider,
        catalog: MarketInstrumentCatalog,
        calendar=None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.catalog = catalog
        self.calendar = calendar

    def issue(
        self,
        *,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
        horizon: int,
        interval: str,
        as_of: datetime,
        limit: int,
        created_by: str,
    ) -> ForecastIssueReceipt:
        states = MarketStateService(self.provider, self.calendar).calculate(
            instrument_ids=instrument_ids,
            start=start,
            end=end,
            interval=interval,
            as_of=as_of,
            limit=limit,
        )
        factor_service = EventImpactFactorService(self.repository)
        runs: list[MarketForecastRun] = []
        created_count = 0
        for state in states:
            instrument = self.catalog.get(state.instrument_id)
            factor = (
                factor_service.snapshot(instrument, as_of=as_of, horizon=horizon)
                if instrument is not None
                else None
            )
            calibration = published_calibration_for(
                self.repository, state.instrument_id, horizon, as_of=as_of
            )
            outlook = MarketOutlookService().preview(
                state,
                horizon=horizon,
                event_factor=factor,
                calibration=calibration,
            )
            input_snapshot = {
                "query": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "interval": interval,
                    "limit": limit,
                },
                "market_state": _json_value(asdict(state)),
                "event_factor": _json_value(asdict(factor)) if factor is not None else None,
                "calibration": (
                    _json_value(asdict(calibration)) if calibration is not None else None
                ),
                "contributions": _json_value([asdict(item) for item in outlook.contributions]),
            }
            source_hash = _source_hash(
                state.instrument_id,
                as_of,
                horizon,
                outlook.rule_version,
                input_snapshot,
            )
            existing = self.repository.find_market_forecast_run_by_source_hash(source_hash)
            if existing is not None:
                runs.append(existing)
                continue
            run = MarketForecastRun(
                id=new_id("mfr"),
                instrument_id=state.instrument_id,
                as_of=as_of,
                horizon=horizon,
                direction=outlook.direction,
                probabilities=outlook.probabilities,
                expected_return_p10=outlook.expected_return_p10,
                expected_return_p50=outlook.expected_return_p50,
                expected_return_p90=outlook.expected_return_p90,
                confidence=outlook.confidence,
                forecast_status=outlook.forecast_status,
                data_status=outlook.data_status,
                calibration_version_id=outlook.calibration_version_id,
                rule_version=outlook.rule_version,
                factor_rule_version=FORECAST_FACTOR_RULE_VERSION,
                factor_source_hash=factor.source_hash if factor is not None else "",
                source_hash=source_hash,
                input_snapshot=input_snapshot,
                created_by=created_by,
                created_at=datetime.now(timezone.utc),
            )
            self.repository.save_market_forecast_run(run)
            runs.append(run)
            created_count += 1
        return ForecastIssueReceipt(
            tuple(runs), created_count, len(runs) - created_count
        )
    def settle(
        self,
        *,
        evaluation_as_of: datetime,
        forecast_ids: list[str] | None = None,
        flat_band: float = 0.003,
    ) -> ForecastSettlementReceipt:
        if evaluation_as_of.tzinfo is None:
            raise ValueError("evaluation_as_of must include a timezone")
        runs = (
            [
                run
                for forecast_id in forecast_ids
                if (run := self.repository.get_market_forecast_run(forecast_id)) is not None
            ]
            if forecast_ids is not None
            else self.repository.list_market_forecast_runs(end=evaluation_as_of, limit=5000)
        )
        candidates = [
            run
            for run in runs
            if run.probabilities is not None
            and self.repository.get_market_forecast_outcome(run.id) is None
        ]
        excluded_count = len(runs) - len(candidates)
        outcomes: list[MarketForecastOutcome] = []
        warnings: list[str] = []
        for instrument_id in sorted({run.instrument_id for run in candidates}):
            instrument_runs = [run for run in candidates if run.instrument_id == instrument_id]
            start = min(run.as_of for run in instrument_runs) - timedelta(days=2)
            result = self.provider.get_bars(
                instrument_ids=[instrument_id],
                start=start,
                end=evaluation_as_of,
                interval="1d",
                as_of=evaluation_as_of,
                limit=5000,
            )
            warnings.extend(result.warnings)
            bars = sorted(result.bars, key=lambda item: item.observed_at)
            for run in instrument_runs:
                state = run.input_snapshot.get("market_state") or {}
                base_price = state.get("latest_close")
                latest_observed_at = _parse_datetime(state.get("latest_observed_at"))
                if base_price is None or latest_observed_at is None:
                    warnings.append(f"{run.id}:base_market_state_missing")
                    continue
                future = [bar for bar in bars if bar.observed_at > latest_observed_at]
                if len(future) < run.horizon:
                    continue
                outcome_bar = future[run.horizon - 1]
                base_adjustment = state.get("latest_adjustment")
                if base_adjustment is not None and base_adjustment != outcome_bar.adjustment:
                    # A qfq base price divided by an unadjusted outcome price is
                    # not a return.  Leave the forecast pending rather than
                    # settling it against an incompatible price basis.
                    warnings.append(
                        f"{run.id}:adjustment_mismatch:"
                        f"{base_adjustment}->{outcome_bar.adjustment}"
                    )
                    continue
                realized_return = outcome_bar.close / float(base_price) - 1
                outcome = MarketForecastOutcome(
                    id=new_id("mfo"),
                    forecast_id=run.id,
                    outcome_observed_at=outcome_bar.observed_at,
                    realized_return=round(realized_return, 8),
                    outcome=label_return(realized_return, flat_band=flat_band),
                    base_price=float(base_price),
                    outcome_price=outcome_bar.close,
                    source=outcome_bar.source,
                    available_at=outcome_bar.available_at or evaluation_as_of,
                    created_at=datetime.now(timezone.utc),
                )
                self.repository.save_market_forecast_outcome(outcome)
                outcomes.append(outcome)
        return ForecastSettlementReceipt(
            evaluated_as_of=evaluation_as_of,
            considered_count=len(runs),
            settled_count=len(outcomes),
            pending_count=len(candidates) - len(outcomes),
            excluded_count=excluded_count,
            outcomes=tuple(outcomes),
            warnings=tuple(dict.fromkeys(warnings)),
        )


def published_calibration_for(
    repository: Repository,
    instrument_id: str,
    horizon: int,
    *,
    as_of: datetime,
) -> MarketCalibrationVersion | None:
    market = instrument_id.split(":", 1)[0]
    exact = repository.list_market_calibration_versions(
        model_key="market-outlook", market=market, horizon=horizon, status="published"
    )
    exact = [item for item in exact if _calibration_visible_at(item, as_of)]
    if exact:
        return exact[0]
    global_versions = repository.list_market_calibration_versions(
        model_key="market-outlook", market="all", horizon=horizon, status="published"
    )
    global_versions = [
        item for item in global_versions if _calibration_visible_at(item, as_of)
    ]
    return global_versions[0] if global_versions else None


def _calibration_visible_at(
    value: MarketCalibrationVersion, as_of: datetime
) -> bool:
    return (
        value.created_at is not None
        and value.created_at <= as_of
        and value.published_at is not None
        and value.published_at <= as_of
        and value.train_end < as_of
    )


def _source_hash(
    instrument_id: str,
    as_of: datetime,
    horizon: int,
    rule_version: str,
    input_snapshot: dict[str, Any],
) -> str:
    payload = json.dumps(
        {
            "instrument_id": instrument_id,
            "as_of": as_of.isoformat(),
            "horizon": horizon,
            "rule_version": rule_version,
            "input_snapshot": input_snapshot,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
