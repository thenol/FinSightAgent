from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.domain import MarketCalibrationVersion
from app.market.forecasting import ForecastLifecycleService, published_calibration_for
from app.market.provider import InMemoryMarketDataProvider, MarketBar, MarketInstrument
from app.market.reference import MarketInstrumentCatalog
from app.platform.repository import InMemoryRepository

AS_OF = datetime(2026, 8, 18, tzinfo=timezone.utc)
INSTRUMENT = MarketInstrument(
    id="cn:index:000300",
    market="cn",
    symbol="000300",
    name="沪深300",
    instrument_type="index",
)


def _bars() -> list[MarketBar]:
    values = []
    for offset in range(-79, 2):
        observed_at = AS_OF + timedelta(days=offset)
        close = 100 + offset * 0.1
        values.append(
            MarketBar(
                instrument_id=INSTRUMENT.id,
                market="cn",
                symbol=INSTRUMENT.symbol,
                interval="1d",
                observed_at=observed_at,
                open=close,
                high=close,
                low=close,
                close=close,
                source="test",
                available_at=observed_at,
            )
        )
    return values


def test_forecast_issue_is_idempotent_and_outcome_settles_once() -> None:
    repository = InMemoryRepository()
    provider = InMemoryMarketDataProvider(_bars())
    service = ForecastLifecycleService(
        repository, provider, MarketInstrumentCatalog((INSTRUMENT,))
    )
    kwargs = {
        "instrument_ids": [INSTRUMENT.id],
        "start": AS_OF - timedelta(days=79),
        "end": AS_OF,
        "horizon": 1,
        "interval": "1d",
        "as_of": AS_OF,
        "limit": 500,
        "created_by": "usr_test",
    }

    first = service.issue(**kwargs)
    second = service.issue(**kwargs)

    assert first.created_count == 1
    assert second.created_count == 0
    assert second.reused_count == 1
    assert first.runs[0].id == second.runs[0].id
    assert first.runs[0].probabilities is not None
    assert first.runs[0].input_snapshot["market_state"]["latest_close"] is not None

    settled = service.settle(
        evaluation_as_of=AS_OF + timedelta(days=1),
        forecast_ids=[first.runs[0].id],
    )
    repeated = service.settle(
        evaluation_as_of=AS_OF + timedelta(days=1),
        forecast_ids=[first.runs[0].id],
    )

    assert settled.settled_count == 1
    assert settled.outcomes[0].forecast_id == first.runs[0].id
    assert repeated.settled_count == 0
    assert repository.get_market_forecast_outcome(first.runs[0].id) is not None


def test_insufficient_forecast_is_persisted_but_excluded_from_settlement() -> None:
    repository = InMemoryRepository()
    provider = InMemoryMarketDataProvider(_bars()[-10:])
    service = ForecastLifecycleService(
        repository, provider, MarketInstrumentCatalog((INSTRUMENT,))
    )

    receipt = service.issue(
        instrument_ids=[INSTRUMENT.id],
        start=AS_OF - timedelta(days=9),
        end=AS_OF,
        horizon=1,
        interval="1d",
        as_of=AS_OF,
        limit=500,
        created_by="usr_test",
    )
    settlement = service.settle(
        evaluation_as_of=AS_OF + timedelta(days=1),
        forecast_ids=[receipt.runs[0].id],
    )

    assert receipt.runs[0].forecast_status == "insufficient_data"
    assert receipt.runs[0].probabilities is None
    assert settlement.excluded_count == 1
    assert settlement.settled_count == 0


def test_settlement_refuses_to_mix_adjustment_conventions() -> None:
    """A qfq base price and an unadjusted outcome price do not form a return.

    Issuance and settlement are separate queries that can be served by different
    providers (bridge returns unadjusted bars, EastMoney daily returns qfq), so
    the price basis must be compared explicitly.
    """

    repository = InMemoryRepository()
    issue_bars = [replace(bar, adjustment="qfq") for bar in _bars()]
    service = ForecastLifecycleService(
        repository,
        InMemoryMarketDataProvider(issue_bars),
        MarketInstrumentCatalog((INSTRUMENT,)),
    )
    receipt = service.issue(
        instrument_ids=[INSTRUMENT.id],
        start=AS_OF - timedelta(days=79),
        end=AS_OF,
        horizon=1,
        interval="1d",
        as_of=AS_OF,
        limit=500,
        created_by="usr_test",
    )
    assert receipt.runs[0].input_snapshot["market_state"]["latest_adjustment"] == "qfq"

    unadjusted = ForecastLifecycleService(
        repository,
        InMemoryMarketDataProvider([replace(bar, adjustment="none") for bar in _bars()]),
        MarketInstrumentCatalog((INSTRUMENT,)),
    )
    settlement = unadjusted.settle(
        evaluation_as_of=AS_OF + timedelta(days=1),
        forecast_ids=[receipt.runs[0].id],
    )

    assert settlement.settled_count == 0
    assert any("adjustment_mismatch:qfq->none" in item for item in settlement.warnings)
    assert repository.get_market_forecast_outcome(receipt.runs[0].id) is None


def test_historical_forecast_cannot_see_future_published_calibration() -> None:
    repository = InMemoryRepository()
    repository.save_market_calibration_version(
        MarketCalibrationVersion(
            id="mcv_future",
            model_key="market-outlook",
            version="future",
            horizon=1,
            market="cn",
            status="published",
            method="temperature_scaling",
            parameters={"temperature": 1.2},
            metrics={},
            train_start=AS_OF - timedelta(days=365),
            train_end=AS_OF + timedelta(days=1),
            sample_count=500,
            created_by="usr_test",
            created_at=AS_OF + timedelta(days=2),
            published_at=AS_OF + timedelta(days=2),
        )
    )

    assert published_calibration_for(
        repository, INSTRUMENT.id, 1, as_of=AS_OF
    ) is None
