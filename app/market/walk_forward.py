"""Purged walk-forward evaluation for market outlook baseline calibration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import mean

from app.market.evaluation import (
    EVALUATION_RULE_VERSION,
    ForecastEvaluationReport,
    ForecastEvaluationSample,
    apply_temperature,
    evaluate_forecasts,
    fit_temperature_scaler,
    purged_walk_forward_folds,
)


@dataclass(frozen=True)
class WalkForwardEvaluationReport:
    fold_count: int
    eligible_sample_count: int
    aggregate: ForecastEvaluationReport
    folds: tuple[ForecastEvaluationReport, ...]
    recommended_temperature: float | None
    rule_version: str = "walk-forward-v1"


def _apply_temperature_to_samples(
    samples: list[ForecastEvaluationSample],
    temperature: float,
) -> list[ForecastEvaluationSample]:
    calibrated: list[ForecastEvaluationSample] = []
    for sample in samples:
        if not sample.eligible or sample.probabilities is None:
            calibrated.append(sample)
            continue
        calibrated.append(
            replace(
                sample,
                probabilities=apply_temperature(sample.probabilities, temperature),
            )
        )
    return calibrated


def evaluate_walk_forward(
    samples: list[ForecastEvaluationSample],
    *,
    minimum_train_size: int = 30,
    test_size: int = 10,
    purge_size: int = 1,
    embargo_size: int = 1,
) -> WalkForwardEvaluationReport:
    """Fit temperature on each train fold and score the held-out test slice."""
    eligible = [item for item in samples if item.eligible and item.probabilities is not None]
    folds = purged_walk_forward_folds(
        len(eligible),
        minimum_train_size=minimum_train_size,
        test_size=test_size,
        purge_size=purge_size,
        embargo_size=embargo_size,
    )
    fold_reports: list[ForecastEvaluationReport] = []
    temperatures: list[float] = []
    held_out: list[ForecastEvaluationSample] = []

    for fold in folds:
        train = eligible[fold.train_start : fold.train_end]
        test = eligible[fold.test_start : fold.test_end]
        fitted = fit_temperature_scaler(train)
        if fitted.status != "fitted":
            continue
        temperatures.append(fitted.temperature)
        calibrated_test = _apply_temperature_to_samples(test, fitted.temperature)
        held_out.extend(calibrated_test)
        fold_reports.append(evaluate_forecasts(calibrated_test))

    aggregate = evaluate_forecasts(held_out) if held_out else evaluate_forecasts([])
    recommended = round(mean(temperatures), 6) if temperatures else None
    return WalkForwardEvaluationReport(
        fold_count=len(fold_reports),
        eligible_sample_count=len(eligible),
        aggregate=replace(aggregate, rule_version=EVALUATION_RULE_VERSION),
        folds=tuple(fold_reports),
        recommended_temperature=recommended,
    )
