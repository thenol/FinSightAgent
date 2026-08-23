"""Explainable baseline outlook calculator.

This is a versioned, deterministic baseline for the forecast contract.  It is
not presented as a calibrated production model until real market history and
walk-forward evaluation are available.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain import MarketCalibrationVersion
from app.market.evaluation import apply_temperature
from app.market.factors import ForecastFactorSnapshot
from app.market.state import MarketStateSnapshot

OUTLOOK_RULE_VERSION = "outlook-baseline-v2"
SUPPORTED_HORIZONS = (1, 3, 5, 20)
REQUIRED_OBSERVATIONS = {1: 60, 3: 90, 5: 120, 20: 250}


@dataclass(frozen=True)
class OutlookContribution:
    source: str
    score: float
    weight: float
    configured_weight: float
    status: str
    confidence: float
    explanation: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class MarketOutlook:
    instrument_id: str
    as_of: datetime
    horizon: int
    direction: str
    probabilities: dict[str, float] | None
    expected_return_p10: float | None
    expected_return_p50: float | None
    expected_return_p90: float | None
    confidence: float
    forecast_status: str
    data_status: str
    rule_version: str
    calibration_version_id: str | None
    calibration_method: str | None
    contributions: tuple[OutlookContribution, ...]
    risks: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    available_observations: int
    required_observations: int
    coverage: float
    factor_coverage: float
    latest_observed_at: datetime | None


class MarketOutlookService:
    def preview(
        self,
        state: MarketStateSnapshot,
        *,
        horizon: int,
        event_score: float | None = None,
        event_factor: ForecastFactorSnapshot | None = None,
        expectation_score: float | None = None,
        priced_in_score: float | None = None,
        calibration: MarketCalibrationVersion | None = None,
    ) -> MarketOutlook:
        if horizon not in SUPPORTED_HORIZONS:
            raise ValueError(f"unsupported outlook horizon: {horizon}")
        configured_weights = {
            "market_state": 0.45,
            "event": 0.25,
            "expectation_gap": 0.20,
            "priced_in": 0.10,
        }
        raw_values = {
            "market_state": state.trend_score,
            "event": event_factor.score if event_factor is not None else event_score,
            "expectation_gap": expectation_score,
            "priced_in": priced_in_score,
        }
        statuses = {
            "market_state": "available" if state.trend_score is not None else "unavailable",
            "event": (
                event_factor.status
                if event_factor is not None
                else ("available" if event_score is not None else "unavailable")
            ),
            "expectation_gap": "available" if expectation_score is not None else "unavailable",
            "priced_in": "available" if priced_in_score is not None else "unavailable",
        }
        scores = {
            key: max(-1.0, min(1.0, value or 0.0)) for key, value in raw_values.items()
        }
        available_weight = sum(
            configured_weights[key] for key in configured_weights if statuses[key] == "available"
        )
        weights = {
            key: (
                configured_weights[key] / available_weight
                if statuses[key] == "available"
                else 0.0
            )
            for key in configured_weights
        }
        factor_coverage = round(available_weight / sum(configured_weights.values()), 4)
        weighted_score = sum(scores[key] * weights[key] for key in weights)
        volatility = state.realized_volatility
        required_observations = REQUIRED_OBSERVATIONS[horizon]
        blocking_reasons = []
        if state.data_status != "ok":
            blocking_reasons.append(f"market_state:{state.data_status}")
        if state.observation_count < required_observations:
            blocking_reasons.append(
                f"observations:{state.observation_count}/{required_observations}"
            )
        if state.trend_score is None or state.realized_volatility is None:
            blocking_reasons.append("required_features_unavailable")
        if blocking_reasons:
            probabilities = None
            direction = "unknown"
            confidence = 0.0
            forecast_status = "insufficient_data"
            data_status = "insufficient_data"
            returns = (None, None, None)
            risks = ("行情数据不足，未生成方向性结论",) + state.data_warnings
        else:
            scale = max(0.08, (volatility or 0.2) * math.sqrt(horizon) / 2)
            standardized_score = weighted_score / scale
            up = math.exp(standardized_score)
            down = math.exp(-standardized_score)
            flat = math.exp(1.0 - min(2.0, abs(standardized_score)))
            total = up + flat + down
            probabilities = {
                "up": round(up / total, 4),
                "flat": round(flat / total, 4),
                "down": round(down / total, 4),
            }
            if calibration is not None and calibration.status == "published":
                temperature = float(calibration.parameters.get("temperature", 1.0))
                probabilities = {
                    key: round(value, 4)
                    for key, value in apply_temperature(probabilities, temperature).items()
                }
            direction = max(probabilities, key=probabilities.get)
            direction = {"up": "positive", "flat": "mixed", "down": "negative"}[direction]
            confidence = round(
                min(
                    0.95,
                    max(0.05, abs(weighted_score) * 0.7 + state.coverage * 0.3)
                    * factor_coverage,
                ),
                4,
            )
            forecast_status = "ready" if calibration is not None else "uncalibrated"
            data_status = "calibrated" if calibration is not None else "baseline_uncalibrated"
            center = weighted_score * (volatility or 0.2) * math.sqrt(horizon)
            spread = (volatility or 0.2) * math.sqrt(horizon) * 0.75
            returns = (center - spread, center, center + spread)
            risks = (
                (f"已应用发布校准版本 {calibration.version}",)
                if calibration is not None
                else ("基线规则尚未经过真实行情概率校准",)
            )
        contributions = tuple(
            OutlookContribution(
                source=key,
                score=round(value, 4),
                weight=weights[key],
                configured_weight=configured_weights[key],
                status=statuses[key],
                confidence=(
                    event_factor.confidence
                    if key == "event" and event_factor is not None
                    else (1.0 if statuses[key] == "available" else 0.0)
                ),
                explanation={
                    "market_state": "趋势状态与近期价格行为",
                    "event": "截至预测时点可见的已批准事件影响信号",
                    "expectation_gap": "实际结果相对市场预期的差异；不可用时不参与加权",
                    "priced_in": "事件在价格中的提前反映程度；不可用时不参与加权",
                }[key],
                provenance=(
                    {
                        "reason": event_factor.reason,
                        "source_hash": event_factor.source_hash,
                        "rule_version": event_factor.rule_version,
                        "sources": [item.__dict__ for item in event_factor.sources],
                    }
                    if key == "event" and event_factor is not None
                    else {}
                ),
            )
            for key, value in scores.items()
        )
        return MarketOutlook(
            instrument_id=state.instrument_id,
            as_of=state.as_of,
            horizon=horizon,
            direction=direction,
            probabilities=probabilities,
            expected_return_p10=_round_or_none(returns[0]),
            expected_return_p50=_round_or_none(returns[1]),
            expected_return_p90=_round_or_none(returns[2]),
            confidence=confidence,
            forecast_status=forecast_status,
            data_status=data_status,
            rule_version=OUTLOOK_RULE_VERSION,
            calibration_version_id=calibration.id if calibration is not None else None,
            calibration_method=calibration.method if calibration is not None else None,
            contributions=contributions,
            risks=risks,
            blocking_reasons=tuple(blocking_reasons),
            available_observations=state.observation_count,
            required_observations=required_observations,
            coverage=state.coverage,
            factor_coverage=factor_coverage,
            latest_observed_at=state.latest_observed_at,
        )


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 6)
