from datetime import datetime, timezone

import pytest

from app.domain import MarketCalibrationVersion
from app.market.outlook import MarketOutlookService
from app.market.state import MarketStateSnapshot


def _state(
    *, status: str = "ok", trend_score: float | None = 0.04,
    observation_count: int = 250,
) -> MarketStateSnapshot:
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    return MarketStateSnapshot(
        instrument_id="cn:000300",
        as_of=now,
        latest_observed_at=now,
        observation_count=observation_count,
        latest_return=0.01,
        trend_score=trend_score,
        realized_volatility=0.2,
        trend="uptrend",
        volatility="normal",
        data_status=status,
        coverage=1.0,
    )


def test_baseline_outlook_is_explainable_and_probabilities_sum_to_one() -> None:
    result = MarketOutlookService().preview(_state(), horizon=5, event_score=0.4)

    assert result.direction == "positive"
    assert sum(result.probabilities.values()) == pytest.approx(1.0, abs=1e-4)
    assert result.expected_return_p10 < result.expected_return_p50 < result.expected_return_p90
    assert result.rule_version == "outlook-baseline-v2"
    assert result.data_status == "baseline_uncalibrated"
    assert {item.source for item in result.contributions} == {
        "market_state",
        "event",
        "expectation_gap",
        "priced_in",
    }


def test_insufficient_market_data_does_not_make_directional_claim() -> None:
    result = MarketOutlookService().preview(
        _state(status="insufficient_data", trend_score=None), horizon=1
    )

    assert result.direction == "unknown"
    assert result.confidence == 0
    assert result.data_status == "insufficient_data"
    assert result.expected_return_p50 is None
    assert result.probabilities is None
    assert result.forecast_status == "insufficient_data"
    assert result.risks


def test_horizon_requires_enough_observations() -> None:
    result = MarketOutlookService().preview(
        _state(observation_count=59), horizon=1
    )

    assert result.probabilities is None
    assert result.required_observations == 60
    assert result.blocking_reasons == ("observations:59/60",)


def test_neutral_signal_can_produce_mixed_direction() -> None:
    result = MarketOutlookService().preview(_state(trend_score=0.0), horizon=1)

    assert result.probabilities is not None
    assert result.direction == "mixed"
    assert result.probabilities["flat"] > result.probabilities["up"]
    assert result.probabilities["flat"] > result.probabilities["down"]


def test_unsupported_horizon_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported outlook horizon"):
        MarketOutlookService().preview(_state(), horizon=10)


def test_published_temperature_calibration_is_applied_and_traced() -> None:
    calibration = MarketCalibrationVersion(
        id="mcv_test",
        model_key="market-outlook",
        version="2026.08.1",
        horizon=1,
        market="cn",
        status="published",
        method="temperature_scaling",
        parameters={"temperature": 2.0},
        metrics={},
        train_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        train_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sample_count=300,
        created_by="usr_test",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        published_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    baseline = MarketOutlookService().preview(_state(), horizon=1)
    calibrated = MarketOutlookService().preview(
        _state(), horizon=1, calibration=calibration
    )

    assert calibrated.forecast_status == "ready"
    assert calibrated.data_status == "calibrated"
    assert calibrated.calibration_version_id == calibration.id
    assert calibrated.probabilities is not None and baseline.probabilities is not None
    assert max(calibrated.probabilities.values()) < max(baseline.probabilities.values())
