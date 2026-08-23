"""Market-data collection worker built on the provider and storage ports."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.market.ingest import MarketIngestBatch, MarketIngestRequest, MarketIngestService
from app.market.provider import MarketDataProvider
from app.market.storage import MarketBatchStore, MarketStorageReceipt


@dataclass(frozen=True)
class MarketWorkerResult:
    batch: MarketIngestBatch
    receipt: MarketStorageReceipt


class MarketDataWorker:
    def __init__(
        self,
        provider: MarketDataProvider,
        store: MarketBatchStore,
        *,
        instrument_ids: tuple[str, ...],
        interval: str = "1d",
        lookback_days: int = 45,
    ) -> None:
        self.provider = provider
        self.store = store
        self.instrument_ids = instrument_ids
        self.interval = interval
        self.lookback_days = lookback_days

    def run_once(self, *, now: datetime | None = None) -> MarketWorkerResult:
        effective_now = now or datetime.now(timezone.utc)
        if effective_now.tzinfo is None:
            raise ValueError("market worker clock requires timezone")
        request = MarketIngestRequest(
            instrument_ids=self.instrument_ids,
            start=effective_now - timedelta(days=self.lookback_days),
            end=effective_now,
            interval=self.interval,
            as_of=effective_now,
        )
        batch = MarketIngestService(self.provider).collect(request)
        receipt = self.store.write(batch)
        return MarketWorkerResult(batch=batch, receipt=receipt)

    async def run_forever(self, stop: asyncio.Event, *, interval_seconds: int = 300) -> None:
        while not stop.is_set():
            await asyncio.to_thread(self.run_once)
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(1, interval_seconds))
            except asyncio.TimeoutError:
                pass
