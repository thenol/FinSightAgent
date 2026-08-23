"""Leakage-safe evaluation primitives for probabilistic market forecasts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Protocol

from app.market.provider import MarketBar

EVALUATION_RULE_VERSION = "forecast-evaluation-v1"
LABEL_RULE_VERSION = "return-band-v1"
CLASSES = ("up", "flat", "down")


class ForecastLike(Protocol):
    instrument_id: str
    as_of: datetime
    horizon: int
    probabilities: dict[str, float] | None


@dataclass(frozen=True)
class ForecastEvaluationSample:
    forecast_id: str
    instrument_id: str
    as_of: datetime
    horizon: int
    probabilities: dict[str, float] | None
    realized_return: float | None
    outcome: str | None
    outcome_observed_at: datetime | None
    eligible: bool
    exclusion_reason: str | None = None
    label_rule_version: str = LABEL_RULE_VERSION


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_confidence: float | None
    empirical_accuracy: float | None


@dataclass(frozen=True)
class ForecastEvaluationReport:
    sample_count: int
    eligible_count: int
    coverage: float | None
    accuracy: float | None
    brier_score: float | None
    log_loss: float | None
    expected_calibration_error: float | None
    class_counts: dict[str, int]
    calibration_bins: tuple[CalibrationBin, ...]
    rule_version: str = EVALUATION_RULE_VERSION


@dataclass(frozen=True)
class ModelComparisonEntry:
    model_key: str
    report: ForecastEvaluationReport


@dataclass(frozen=True)
class ChampionChallengerReport:
    comparable_sample_count: int
    entries: tuple[ModelComparisonEntry, ...]
    incumbent_model_key: str | None
    recommended_model_key: str | None
    decision: str
    decision_reasons: tuple[str, ...]
    rule_version: str = "forecast-champion-challenger-v1"


@dataclass(frozen=True)
class WalkForwardFold:
    fold: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    purge_size: int
    embargo_size: int


@dataclass(frozen=True)
class TemperatureCalibration:
    temperature: float
    fit_sample_count: int
    log_loss_before: float
    log_loss_after: float
    status: str
    rule_version: str = "temperature-scaling-v1"


def label_return(realized_return: float, *, flat_band: float = 0.003) -> str:
    if flat_band < 0:
        raise ValueError("flat_band must be non-negative")
    if realized_return > flat_band:
        return "up"
    if realized_return < -flat_band:
        return "down"
    return "flat"


def materialize_evaluation_samples(
    forecasts: list[ForecastLike],
    bars: list[MarketBar],
    *,
    evaluation_as_of: datetime,
    flat_band: float = 0.003,
) -> list[ForecastEvaluationSample]:
    """Join forecasts to later trading bars without crossing availability cutoffs."""
    if evaluation_as_of.tzinfo is None:
        raise ValueError("evaluation_as_of must include a timezone")
    by_instrument: dict[str, list[MarketBar]] = {}
    for bar in bars:
        if bar.observed_at <= evaluation_as_of and (
            bar.available_at is None or bar.available_at <= evaluation_as_of
        ):
            by_instrument.setdefault(bar.instrument_id, []).append(bar)
    for values in by_instrument.values():
        values.sort(key=lambda item: item.observed_at)

    samples: list[ForecastEvaluationSample] = []
    for forecast in forecasts:
        forecast_id = _forecast_identity(forecast)
        if forecast.probabilities is None:
            samples.append(
                ForecastEvaluationSample(
                    forecast_id,
                    forecast.instrument_id,
                    forecast.as_of,
                    forecast.horizon,
                    None,
                    None,
                    None,
                    None,
                    False,
                    "forecast_insufficient_data",
                )
            )
            continue
        visible_at_forecast = [
            bar
            for bar in by_instrument.get(forecast.instrument_id, [])
            if bar.observed_at <= forecast.as_of
            and (bar.available_at is None or bar.available_at <= forecast.as_of)
        ]
        if not visible_at_forecast:
            samples.append(
                ForecastEvaluationSample(
                    forecast_id,
                    forecast.instrument_id,
                    forecast.as_of,
                    forecast.horizon,
                    forecast.probabilities,
                    None,
                    None,
                    None,
                    False,
                    "base_bar_unavailable_as_of",
                )
            )
            continue
        base = visible_at_forecast[-1]
        future = [
            bar
            for bar in by_instrument.get(forecast.instrument_id, [])
            if bar.observed_at > base.observed_at
        ]
        if len(future) < forecast.horizon:
            samples.append(
                ForecastEvaluationSample(
                    forecast_id,
                    forecast.instrument_id,
                    forecast.as_of,
                    forecast.horizon,
                    forecast.probabilities,
                    None,
                    None,
                    None,
                    False,
                    "outcome_not_observed",
                )
            )
            continue
        outcome_bar = future[forecast.horizon - 1]
        realized_return = outcome_bar.close / base.close - 1
        samples.append(
            ForecastEvaluationSample(
                forecast_id=forecast_id,
                instrument_id=forecast.instrument_id,
                as_of=forecast.as_of,
                horizon=forecast.horizon,
                probabilities=forecast.probabilities,
                realized_return=round(realized_return, 8),
                outcome=label_return(realized_return, flat_band=flat_band),
                outcome_observed_at=outcome_bar.observed_at,
                eligible=True,
            )
        )
    return samples


def evaluate_forecasts(
    samples: list[ForecastEvaluationSample], *, bin_count: int = 10
) -> ForecastEvaluationReport:
    if bin_count < 2:
        raise ValueError("bin_count must be at least 2")
    eligible = [
        item
        for item in samples
        if item.eligible and item.probabilities is not None and item.outcome in CLASSES
    ]
    if not eligible:
        return ForecastEvaluationReport(
            sample_count=len(samples),
            eligible_count=0,
            coverage=0.0 if samples else None,
            accuracy=None,
            brier_score=None,
            log_loss=None,
            expected_calibration_error=None,
            class_counts={key: 0 for key in CLASSES},
            calibration_bins=_empty_bins(bin_count),
        )

    rows = [(_validated_probabilities(item.probabilities), str(item.outcome)) for item in eligible]
    accuracy = mean(
        max(probabilities, key=probabilities.get) == outcome for probabilities, outcome in rows
    )
    brier = mean(
        sum((probabilities[key] - float(key == outcome)) ** 2 for key in CLASSES)
        for probabilities, outcome in rows
    )
    log_loss = mean(
        -math.log(max(1e-12, probabilities[outcome])) for probabilities, outcome in rows
    )
    bins = _calibration_bins(rows, bin_count)
    ece = sum(
        item.count / len(rows) * abs(item.mean_confidence - item.empirical_accuracy)
        for item in bins
        if item.count and item.mean_confidence is not None and item.empirical_accuracy is not None
    )
    return ForecastEvaluationReport(
        sample_count=len(samples),
        eligible_count=len(eligible),
        coverage=round(len(eligible) / len(samples), 6) if samples else 1.0,
        accuracy=round(accuracy, 6),
        brier_score=round(brier, 6),
        log_loss=round(log_loss, 6),
        expected_calibration_error=round(ece, 6),
        class_counts={key: sum(outcome == key for _, outcome in rows) for key in CLASSES},
        calibration_bins=bins,
    )


def compare_forecast_models(
    samples_by_model: dict[str, list[ForecastEvaluationSample]],
    *,
    incumbent_model_key: str | None = None,
    minimum_comparable_samples: int = 100,
) -> ChampionChallengerReport:
    """Compare models only on the exact same settled forecast slots.

    This is a recommendation function, not an online promotion mechanism. A
    release still requires the calibration publication gates and reviewer action.
    """
    eligible_by_model = {
        key: {
            (item.instrument_id, item.as_of, item.horizon): item
            for item in values
            if item.eligible and item.probabilities is not None and item.outcome in CLASSES
        }
        for key, values in samples_by_model.items()
    }
    non_empty = {key: values for key, values in eligible_by_model.items() if values}
    if len(non_empty) < 2:
        return ChampionChallengerReport(
            comparable_sample_count=0,
            entries=(),
            incumbent_model_key=incumbent_model_key,
            recommended_model_key=None,
            decision="insufficient_models",
            decision_reasons=("at_least_two_models_with_settled_forecasts_required",),
        )
    slots = set.intersection(*(set(values) for values in non_empty.values()))
    ordered_keys = sorted(non_empty)
    entries = tuple(
        ModelComparisonEntry(
            model_key=key,
            report=evaluate_forecasts([non_empty[key][slot] for slot in sorted(slots)]),
        )
        for key in ordered_keys
    )
    if len(slots) < minimum_comparable_samples:
        return ChampionChallengerReport(
            comparable_sample_count=len(slots),
            entries=entries,
            incumbent_model_key=incumbent_model_key,
            recommended_model_key=None,
            decision="insufficient_comparable_samples",
            decision_reasons=(f"minimum_comparable_samples={minimum_comparable_samples}",),
        )
    ranked = sorted(
        entries,
        key=lambda item: (
            item.report.brier_score if item.report.brier_score is not None else float("inf"),
            item.report.log_loss if item.report.log_loss is not None else float("inf"),
            item.report.expected_calibration_error
            if item.report.expected_calibration_error is not None
            else float("inf"),
            -(item.report.accuracy or 0.0),
            item.model_key,
        ),
    )
    recommended = ranked[0]
    incumbent = next((item for item in entries if item.model_key == incumbent_model_key), None)
    if incumbent is None:
        return ChampionChallengerReport(
            comparable_sample_count=len(slots),
            entries=entries,
            incumbent_model_key=incumbent_model_key,
            recommended_model_key=recommended.model_key,
            decision="recommend_review",
            decision_reasons=("no_declared_incumbent_in_comparison",),
        )
    if recommended.model_key == incumbent.model_key:
        return ChampionChallengerReport(
            comparable_sample_count=len(slots),
            entries=entries,
            incumbent_model_key=incumbent.model_key,
            recommended_model_key=incumbent.model_key,
            decision="retain_incumbent",
            decision_reasons=("incumbent_has_best_joint_probability_metrics",),
        )
    challenger = recommended.report
    baseline = incumbent.report
    gates = {
        "brier_improvement": (baseline.brier_score or float("inf"))
        - (challenger.brier_score or float("inf"))
        >= 0.01,
        "log_loss_not_worse": (challenger.log_loss or float("inf"))
        < (baseline.log_loss or float("inf")),
        "ece_not_worse": (challenger.expected_calibration_error or float("inf"))
        <= (baseline.expected_calibration_error or float("inf")),
        "accuracy_not_materially_worse": (challenger.accuracy or 0.0)
        >= (baseline.accuracy or 0.0) - 0.01,
    }
    return ChampionChallengerReport(
        comparable_sample_count=len(slots),
        entries=entries,
        incumbent_model_key=incumbent.model_key,
        recommended_model_key=recommended.model_key if all(gates.values()) else incumbent.model_key,
        decision="recommend_challenger" if all(gates.values()) else "retain_incumbent",
        decision_reasons=tuple(key for key, passed in gates.items() if not passed)
        or ("all_challenger_gates_passed",),
    )


def purged_walk_forward_folds(
    sample_count: int,
    *,
    minimum_train_size: int,
    test_size: int,
    purge_size: int,
    embargo_size: int,
) -> tuple[WalkForwardFold, ...]:
    if min(sample_count, minimum_train_size, test_size) < 1:
        raise ValueError("sample and window sizes must be positive")
    if min(purge_size, embargo_size) < 0:
        raise ValueError("purge and embargo sizes must be non-negative")
    folds: list[WalkForwardFold] = []
    test_start = minimum_train_size + purge_size
    while test_start + test_size <= sample_count:
        train_end = test_start - purge_size
        folds.append(
            WalkForwardFold(
                fold=len(folds) + 1,
                train_start=0,
                train_end=train_end,
                test_start=test_start,
                test_end=test_start + test_size,
                purge_size=purge_size,
                embargo_size=embargo_size,
            )
        )
        test_start += test_size + embargo_size
    return tuple(folds)


def fit_temperature_scaler(
    samples: list[ForecastEvaluationSample],
) -> TemperatureCalibration:
    rows = [
        (_validated_probabilities(item.probabilities), str(item.outcome))
        for item in samples
        if item.eligible and item.probabilities is not None and item.outcome in CLASSES
    ]
    if len(rows) < 30:
        return TemperatureCalibration(1.0, len(rows), 0.0, 0.0, "insufficient_data")
    candidates = [value / 20 for value in range(10, 61)]
    losses = {temperature: _temperature_log_loss(rows, temperature) for temperature in candidates}
    best = min(losses, key=losses.get)
    before = _temperature_log_loss(rows, 1.0)
    return TemperatureCalibration(
        temperature=best,
        fit_sample_count=len(rows),
        log_loss_before=round(before, 6),
        log_loss_after=round(losses[best], 6),
        status="fitted",
    )


def apply_temperature(probabilities: dict[str, float], temperature: float) -> dict[str, float]:
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    values = _validated_probabilities(probabilities)
    logits = {key: math.log(max(1e-12, values[key])) / temperature for key in CLASSES}
    offset = max(logits.values())
    exponentials = {key: math.exp(logits[key] - offset) for key in CLASSES}
    total = sum(exponentials.values())
    return {key: exponentials[key] / total for key in CLASSES}


def _temperature_log_loss(rows: list[tuple[dict[str, float], str]], temperature: float) -> float:
    return mean(
        -math.log(max(1e-12, apply_temperature(probabilities, temperature)[outcome]))
        for probabilities, outcome in rows
    )


def _forecast_identity(forecast: ForecastLike) -> str:
    payload = (
        f"{forecast.instrument_id}|{forecast.as_of.isoformat()}|{forecast.horizon}|"
        f"{forecast.probabilities}"
    )
    return f"fcs_{hashlib.sha256(payload.encode()).hexdigest()[:24]}"


def _validated_probabilities(probabilities: dict[str, float]) -> dict[str, float]:
    if set(probabilities) != set(CLASSES):
        raise ValueError("probabilities must contain up, flat and down")
    if any(not math.isfinite(value) or value < 0 for value in probabilities.values()):
        raise ValueError("probabilities must be finite and non-negative")
    total = sum(probabilities.values())
    if total <= 0:
        raise ValueError("probability total must be positive")
    return {key: probabilities[key] / total for key in CLASSES}


def _calibration_bins(
    rows: list[tuple[dict[str, float], str]], bin_count: int
) -> tuple[CalibrationBin, ...]:
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bin_count)]
    for probabilities, outcome in rows:
        prediction = max(probabilities, key=probabilities.get)
        confidence = probabilities[prediction]
        index = min(bin_count - 1, int(confidence * bin_count))
        buckets[index].append((confidence, prediction == outcome))
    return tuple(
        CalibrationBin(
            lower=index / bin_count,
            upper=(index + 1) / bin_count,
            count=len(bucket),
            mean_confidence=(round(mean(value for value, _ in bucket), 6) if bucket else None),
            empirical_accuracy=(
                round(mean(correct for _, correct in bucket), 6) if bucket else None
            ),
        )
        for index, bucket in enumerate(buckets)
    )


def _empty_bins(bin_count: int) -> tuple[CalibrationBin, ...]:
    return tuple(
        CalibrationBin(index / bin_count, (index + 1) / bin_count, 0, None, None)
        for index in range(bin_count)
    )
