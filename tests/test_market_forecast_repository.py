from datetime import datetime, timedelta, timezone

from app.domain import (
    MarketCalibrationVersion,
    MarketForecastOutcome,
    MarketForecastRun,
)
from app.platform.repository import SqlAlchemyRepository

NOW = datetime(2026, 8, 18, tzinfo=timezone.utc)
SCHEMA_MAP = {
    "ingestion": None,
    "events": None,
    "evidence": None,
    "publishing": None,
    "platform": None,
    "analysis": None,
}


def test_sqlalchemy_forecast_lifecycle_and_calibration_registry(tmp_path) -> None:
    repository = SqlAlchemyRepository(
        f"sqlite:///{tmp_path / 'forecast.db'}", schema_translate_map=SCHEMA_MAP
    )
    repository.create_schema_for_tests()
    run = MarketForecastRun(
        id="mfr_test",
        instrument_id="cn:index:000300",
        as_of=NOW,
        horizon=1,
        direction="positive",
        probabilities={"up": 0.6, "flat": 0.3, "down": 0.1},
        expected_return_p10=-0.01,
        expected_return_p50=0.01,
        expected_return_p90=0.03,
        confidence=0.6,
        forecast_status="uncalibrated",
        data_status="baseline_uncalibrated",
        calibration_version_id=None,
        rule_version="outlook-baseline-v2",
        factor_rule_version="forecast-factor-v1",
        factor_source_hash="factor-hash",
        source_hash="source-hash",
        input_snapshot={"market_state": {"latest_close": 100}},
        created_by="usr_test",
        created_at=NOW,
    )
    outcome = MarketForecastOutcome(
        id="mfo_test",
        forecast_id=run.id,
        outcome_observed_at=NOW + timedelta(days=1),
        realized_return=0.02,
        outcome="up",
        base_price=100,
        outcome_price=102,
        source="test",
        available_at=NOW + timedelta(days=1),
        created_at=NOW + timedelta(days=1),
    )
    calibration = MarketCalibrationVersion(
        id="mcv_test",
        model_key="market-outlook",
        version="2026.08.1",
        horizon=1,
        market="cn",
        status="draft",
        method="temperature_scaling",
        parameters={"temperature": 1.2},
        metrics={"log_loss": 0.8},
        train_start=NOW - timedelta(days=365),
        train_end=NOW,
        sample_count=120,
        created_by="usr_test",
        created_at=NOW,
    )

    repository.save_market_forecast_run(run)
    repository.save_market_forecast_run(run)
    repository.save_market_forecast_outcome(outcome)
    repository.save_market_forecast_outcome(outcome)
    repository.save_market_calibration_version(calibration)

    stored_run = repository.find_market_forecast_run_by_source_hash("source-hash")
    assert stored_run is not None and stored_run.id == run.id
    assert [item.id for item in repository.list_market_forecast_runs(horizon=1)] == [run.id]
    stored_outcome = repository.get_market_forecast_outcome(run.id)
    assert stored_outcome is not None and stored_outcome.id == outcome.id
    assert [
        item.id for item in repository.list_market_calibration_versions(status="draft")
    ] == [calibration.id]
