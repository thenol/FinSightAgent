"""Persistence ports for normalized market batches."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from app.market.ingest import MarketIngestBatch

CLICKHOUSE_MARKET_BARS_DDL = """
CREATE TABLE IF NOT EXISTS market_bars_canonical (
    instrument_id String, market LowCardinality(String), symbol String,
    interval LowCardinality(String), observed_at DateTime64(3, 'UTC'),
    open Float64, high Float64, low Float64, close Float64,
    volume Nullable(Float64), amount Nullable(Float64), turnover Nullable(Float64),
    vwap Nullable(Float64), adjustment LowCardinality(String), source String,
    available_at Nullable(DateTime64(3, 'UTC')), ingested_at DateTime64(3, 'UTC'),
    ingest_run_id String
) ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (instrument_id, interval, observed_at, source)
"""


@dataclass(frozen=True)
class MarketStorageReceipt:
    run_id: str
    location: str
    checksum: str
    bar_count: int
    persisted_at: datetime
    status: str = "ok"
    destinations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class MarketBatchStore(Protocol):
    def write(self, batch: MarketIngestBatch) -> MarketStorageReceipt: ...


class LocalMarketBatchStore:
    """Atomic JSON archive with an object-storage-compatible layout."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def write(self, batch: MarketIngestBatch) -> MarketStorageReceipt:
        core = {"run": _json_value(batch.run), "bars": [_json_value(bar) for bar in batch.bars]}
        core_content = json.dumps(
            core, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        payload = {
            "schema_version": "market-archive-v2",
            "content_hash": hashlib.sha256(core_content).hexdigest(),
            **core,
        }
        content = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        checksum = hashlib.sha256(content).hexdigest()
        path = (
            self.root
            / "market-batches"
            / batch.run.started_at.strftime("%Y/%m/%d")
            / f"{batch.run.run_id}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(path)
        return MarketStorageReceipt(
            run_id=batch.run.run_id,
            location=str(path.resolve()),
            checksum=checksum,
            bar_count=len(batch.bars),
            persisted_at=datetime.now(timezone.utc),
        )


class ClickHouseMarketBarStore:
    """Thin adapter around a clickhouse-connect compatible client."""

    def __init__(self, client: Any, table: str = "market_bars_canonical") -> None:
        self.client = client
        self.table = table

    def write(self, batch: MarketIngestBatch) -> MarketStorageReceipt:
        rows = [_bar_row(bar, batch.run.run_id) for bar in batch.bars]
        if rows:
            self.client.insert(self.table, rows, column_names=_BAR_COLUMNS)
        content = json.dumps(rows, default=str, sort_keys=True).encode()
        return MarketStorageReceipt(
            run_id=batch.run.run_id,
            location=f"clickhouse://{self.table}",
            checksum=hashlib.sha256(content).hexdigest(),
            bar_count=len(rows),
            persisted_at=datetime.now(timezone.utc),
        )


class MirroredMarketBatchStore:
    """Persist to a durable primary and mirror to secondary analytical stores."""

    def __init__(self, primary: MarketBatchStore, *mirrors: MarketBatchStore) -> None:
        self.primary = primary
        self.mirrors = mirrors

    def write(self, batch: MarketIngestBatch) -> MarketStorageReceipt:
        primary_receipt = self.primary.write(batch)
        receipts = [primary_receipt]
        warnings: list[str] = []
        for index, mirror in enumerate(self.mirrors):
            try:
                receipts.append(mirror.write(batch))
            except Exception as exc:
                warnings.append(f"mirror_{index}_failed:{type(exc).__name__}")
        destinations = tuple(receipt.location for receipt in receipts)
        digest = hashlib.sha256(
            "|".join(receipt.checksum for receipt in receipts).encode()
        ).hexdigest()
        return MarketStorageReceipt(
            run_id=batch.run.run_id,
            location=primary_receipt.location,
            checksum=digest,
            bar_count=primary_receipt.bar_count,
            persisted_at=datetime.now(timezone.utc),
            status="degraded" if warnings else "ok",
            destinations=destinations,
            warnings=tuple(warnings),
        )


def build_clickhouse_store(url: str) -> ClickHouseMarketBarStore:
    """Build and initialize the official HTTP ClickHouse client."""
    import clickhouse_connect

    parsed = urlparse(url)
    if parsed.scheme != "clickhouse" or not parsed.hostname:
        raise ValueError("invalid ClickHouse URL")
    client = clickhouse_connect.get_client(
        host=parsed.hostname,
        port=parsed.port or 8123,
        username=parsed.username or "default",
        password=parsed.password or "",
        database=parsed.path.lstrip("/") or "default",
    )
    client.command(CLICKHOUSE_MARKET_BARS_DDL)
    return ClickHouseMarketBarStore(client)


def build_market_batch_store(*, mode: str, archive_root: str, clickhouse_url: str):
    local = LocalMarketBatchStore(archive_root)
    if mode == "local":
        return local
    clickhouse = build_clickhouse_store(clickhouse_url)
    if mode == "clickhouse":
        return clickhouse
    if mode == "dual":
        return MirroredMarketBatchStore(local, clickhouse)
    raise ValueError(f"unsupported market store mode: {mode}")


_BAR_COLUMNS = (
    "instrument_id",
    "market",
    "symbol",
    "interval",
    "observed_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "turnover",
    "vwap",
    "adjustment",
    "source",
    "available_at",
    "ingested_at",
    "ingest_run_id",
)


def _bar_row(bar: Any, run_id: str) -> tuple[Any, ...]:
    return tuple(getattr(bar, column) for column in _BAR_COLUMNS[:-1]) + (run_id,)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
