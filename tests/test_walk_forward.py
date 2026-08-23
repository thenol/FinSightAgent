"""Walk-forward evaluation for market outlook calibration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.market.evaluation import ForecastEvaluationSample
from app.market.walk_forward import evaluate_walk_forward

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sample(index: int, outcome: str, probabilities: dict[str, float]) -> ForecastEvaluationSample:
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


def test_walk_forward_fits_temperature_and_scores_held_out_folds() -> None:
    samples = [
        _sample(index, outcome, {"up": 0.7, "flat": 0.2, "down": 0.1})
        for index, outcome in enumerate(
            ["up", "up", "down", "flat", "up"] * 20
        )
    ]
    report = evaluate_walk_forward(
        samples,
        minimum_train_size=30,
        test_size=10,
        purge_size=1,
        embargo_size=1,
    )
    assert report.fold_count >= 1
    assert report.eligible_sample_count == len(samples)
    assert report.recommended_temperature is not None
    assert report.aggregate.eligible_count > 0
