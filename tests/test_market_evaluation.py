from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.market.evaluation import (
    ForecastEvaluationSample,
    apply_temperature,
    compare_forecast_models,
    evaluate_forecasts,
    fit_temperature_scaler,
    label_return,
    materialize_evaluation_samples,
    purged_walk_forward_folds,
)
from app.market.provider import MarketBar

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass
class _Forecast:
    instrument_id: str
    as_of: datetime
    horizon: int
    probabilities: dict[str, float] | None


def _sample(index: int, outcome: str, probabilities: dict[str, float]):
    return ForecastEvaluationSample(
        forecast_id=f"fc_{index}",
        instrument_id="cn:index:000300",
        as_of=NOW + timedelta(days=index),
        horizon=1,
        probabilities=probabilities,
        realized_return=0.01,
        outcome=outcome,
        outcome_observed_at=NOW + timedelta(days=index + 1),
        eligible=True,
    )


def test_return_label_uses_explicit_flat_band() -> None:
    assert label_return(0.004) == "up"
    assert label_return(-0.004) == "down"
    assert label_return(0.002) == "flat"


def test_evaluation_reports_probability_quality_and_coverage() -> None:
    samples = [
        _sample(0, "up", {"up": 0.8, "flat": 0.1, "down": 0.1}),
        _sample(1, "down", {"up": 0.2, "flat": 0.2, "down": 0.6}),
        ForecastEvaluationSample(
            "fc_2",
            "cn:index:000300",
            NOW,
            1,
            None,
            None,
            None,
            None,
            False,
            "forecast_insufficient_data",
        ),
    ]

    report = evaluate_forecasts(samples)

    assert report.sample_count == 3
    assert report.eligible_count == 2
    assert report.coverage == pytest.approx(2 / 3, abs=1e-6)
    assert report.accuracy == 1.0
    assert report.brier_score is not None and report.brier_score < 0.3
    assert report.log_loss is not None


def test_walk_forward_folds_apply_purge_and_embargo() -> None:
    folds = purged_walk_forward_folds(
        130, minimum_train_size=60, test_size=20, purge_size=5, embargo_size=3
    )

    assert len(folds) == 2
    assert folds[0].train_end == 60
    assert folds[0].test_start == 65
    assert folds[1].test_start - folds[0].test_end == 3
    assert folds[0].train_end <= folds[0].test_start - folds[0].purge_size


def test_temperature_scaler_requires_enough_samples_and_normalizes_output() -> None:
    small = [_sample(index, "up", {"up": 0.7, "flat": 0.2, "down": 0.1}) for index in range(5)]
    assert fit_temperature_scaler(small).status == "insufficient_data"

    probabilities = apply_temperature({"up": 0.8, "flat": 0.1, "down": 0.1}, 2.0)
    assert sum(probabilities.values()) == pytest.approx(1.0)
    assert probabilities["up"] < 0.8


def test_materialize_samples_uses_trading_horizon_and_availability_time() -> None:
    forecast = _Forecast("cn:index:000300", NOW, 2, {"up": 0.6, "flat": 0.3, "down": 0.1})
    bars = [
        MarketBar(
            "cn:index:000300",
            "cn",
            "000300",
            "1d",
            NOW + timedelta(days=index),
            close,
            close,
            close,
            close,
            source="test",
            available_at=available_at,
        )
        for index, close, available_at in [
            (-1, 100.0, NOW - timedelta(days=1)),
            (1, 101.0, NOW + timedelta(days=1)),
            (2, 104.0, NOW + timedelta(days=2)),
        ]
    ]

    early = materialize_evaluation_samples(
        [forecast], bars, evaluation_as_of=NOW + timedelta(days=1)
    )[0]
    mature = materialize_evaluation_samples(
        [forecast], bars, evaluation_as_of=NOW + timedelta(days=2)
    )[0]

    assert early.eligible is False
    assert early.exclusion_reason == "outcome_not_observed"
    assert mature.eligible is True
    assert mature.realized_return == pytest.approx(0.04)
    assert mature.outcome == "up"


def test_champion_challenger_uses_only_shared_settled_slots() -> None:
    incumbent = [
        _sample(index, "up", {"up": 0.55, "flat": 0.25, "down": 0.20}) for index in range(100)
    ]
    challenger = [
        _sample(index, "up", {"up": 0.80, "flat": 0.10, "down": 0.10}) for index in range(100)
    ]
    challenger.append(_sample(101, "up", {"up": 0.99, "flat": 0.005, "down": 0.005}))

    comparison = compare_forecast_models(
        {"incumbent": incumbent, "challenger": challenger},
        incumbent_model_key="incumbent",
    )

    assert comparison.comparable_sample_count == 100
    assert comparison.decision == "recommend_challenger"
    assert comparison.recommended_model_key == "challenger"
