#!/usr/bin/env python3
"""Run walk-forward calibration on synthetic in-memory market history."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market.evaluation import materialize_evaluation_samples  # noqa: E402
from app.market.forecasting import ForecastLifecycleService  # noqa: E402
from app.market.provider import (  # noqa: E402
    InMemoryMarketDataProvider,
    MarketBar,
    MarketInstrument,
)
from app.market.reference import MarketInstrumentCatalog  # noqa: E402
from app.market.walk_forward import evaluate_walk_forward  # noqa: E402
from app.platform.repository import InMemoryRepository  # noqa: E402

AS_OF = datetime(2026, 8, 18, tzinfo=timezone.utc)
INSTRUMENT = MarketInstrument(
    id="cn:index:000300",
    market="cn",
    symbol="000300",
    name="沪深300",
    instrument_type="index",
)


def _bars() -> list[MarketBar]:
    rows: list[MarketBar] = []
    for offset in range(-119, 2):
        observed_at = AS_OF + timedelta(days=offset)
        close = 100 + offset * 0.08
        rows.append(
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
                source="walk-forward-script",
                available_at=observed_at,
            )
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward outlook baseline calibration")
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    repository = InMemoryRepository()
    bars = _bars()
    provider = InMemoryMarketDataProvider(bars)
    service = ForecastLifecycleService(
        repository, provider, MarketInstrumentCatalog((INSTRUMENT,))
    )
    receipt = service.issue(
        instrument_ids=[INSTRUMENT.id],
        start=AS_OF - timedelta(days=119),
        end=AS_OF,
        horizon=args.horizon,
        interval="1d",
        as_of=AS_OF,
        limit=500,
        created_by="walk_forward_script",
    )
    service.settle(
        evaluation_as_of=AS_OF + timedelta(days=args.horizon),
        forecast_ids=[run.id for run in receipt.runs],
    )
    samples = materialize_evaluation_samples(
        list(receipt.runs),
        bars,
        evaluation_as_of=AS_OF + timedelta(days=args.horizon),
    )
    report = evaluate_walk_forward(samples)
    payload = {
        "status": "completed",
        "forecast_count": len(receipt.runs),
        "recommended_temperature": report.recommended_temperature,
        "fold_count": report.fold_count,
        "eligible_sample_count": report.eligible_sample_count,
        "aggregate": asdict(report.aggregate),
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
