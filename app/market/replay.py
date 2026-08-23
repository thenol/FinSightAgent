"""Point-in-time market-data reader over immutable local archives."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.market.provider import (
    MarketBar,
    MarketDataCapability,
    MarketDataResult,
    MarketObservation,
    MarketSnapshot,
)


class LocalArchiveMarketDataProvider:
    def __init__(self, root: str, *, require_verified: bool = True) -> None:
        self.root = Path(root)
        self.require_verified = require_verified

    @property
    def capability(self) -> MarketDataCapability:
        return MarketDataCapability(
            provider="local-market-archive",
            status="configured",
            supported_markets=["cn", "hk", "us"],
            supported_intervals=["1d", "5m"],
        )

    def get_bars(
        self,
        *,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
        interval: str,
        as_of: datetime,
        limit: int,
    ) -> MarketDataResult:
        if any(item.tzinfo is None for item in (start, end, as_of)):
            raise ValueError("archive replay timestamps require timezone")
        if end < start or limit < 1:
            raise ValueError("archive replay range or limit is invalid")
        bars, warnings = self._visible_bars(as_of)
        selected = [
            item
            for item in bars
            if item.instrument_id in instrument_ids
            and item.interval == interval
            and start <= item.observed_at <= end
        ]
        limited: list[MarketBar] = []
        for instrument_id in instrument_ids:
            values = sorted(
                (item for item in selected if item.instrument_id == instrument_id),
                key=lambda item: item.observed_at,
            )
            limited.extend(values[-limit:])
        missing = sorted(set(instrument_ids) - {item.instrument_id for item in limited})
        warnings.extend(f"{item}:archive_data_missing" for item in missing)
        return MarketDataResult(
            status="ok" if limited and not warnings else "degraded",
            bars=limited,
            capability=self.capability,
            warnings=warnings,
        )

    def get_snapshots(self, *, instrument_ids: list[str], as_of: datetime) -> MarketDataResult:
        bars, warnings = self._visible_bars(as_of)
        snapshots: list[MarketSnapshot] = []
        for instrument_id in instrument_ids:
            values = sorted(
                (item for item in bars if item.instrument_id == instrument_id),
                key=lambda item: item.observed_at,
            )
            if not values:
                warnings.append(f"{instrument_id}:archive_data_missing")
                continue
            item = values[-1]
            snapshots.append(
                MarketSnapshot(
                    instrument_id=item.instrument_id,
                    market=item.market,
                    symbol=item.symbol,
                    observed_at=item.observed_at,
                    last=item.close,
                    volume=item.volume,
                    amount=item.amount,
                    source=item.source,
                    available_at=item.available_at,
                )
            )
        return MarketDataResult(
            status="ok" if snapshots and not warnings else "degraded",
            snapshots=snapshots,
            capability=self.capability,
            warnings=warnings,
        )

    def query(
        self,
        *,
        security_ids: list[str],
        start: datetime | None,
        end: datetime | None,
        as_of: datetime | None,
        limit: int,
    ) -> MarketDataResult:
        if start is None or end is None or as_of is None:
            raise ValueError("archive query requires start, end and as_of")
        result = self.get_bars(
            instrument_ids=security_ids,
            start=start,
            end=end,
            interval="1d",
            as_of=as_of,
            limit=limit,
        )
        result.observations.extend(
            MarketObservation(
                security_id=item.instrument_id,
                observed_at=item.observed_at,
                values={"close": item.close},
                source=item.source,
            )
            for item in result.bars
        )
        return result

    def get_calendar(self, **_: Any) -> MarketDataResult:
        return MarketDataResult(
            status="unavailable",
            capability=self.capability,
            warnings=["archive_calendar_not_stored"],
        )

    def _visible_bars(self, as_of: datetime) -> tuple[list[MarketBar], list[str]]:
        if as_of.tzinfo is None:
            raise ValueError("archive replay as_of requires timezone")
        warnings: list[str] = []
        latest: dict[tuple[str, str, datetime, str], MarketBar] = {}
        for path in sorted((self.root / "market-batches").glob("**/*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not self._verified(payload):
                    warnings.append(f"{path.name}:archive_integrity_unverified")
                    if self.require_verified:
                        continue
                run_finished_at = _parse_datetime((payload.get("run") or {}).get("finished_at"))
                for raw in payload.get("bars", []):
                    bar = _market_bar(raw)
                    knowledge_at = bar.ingested_at or run_finished_at
                    if knowledge_at is None or knowledge_at > as_of:
                        continue
                    if bar.available_at is not None and bar.available_at > as_of:
                        continue
                    key = (bar.instrument_id, bar.interval, bar.observed_at, bar.source)
                    current = latest.get(key)
                    if (
                        current is None
                        or (current.ingested_at or current.observed_at) < knowledge_at
                    ):
                        latest[key] = bar
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                warnings.append(f"{path.name}:archive_corrupt")
        return sorted(
            latest.values(), key=lambda item: (item.instrument_id, item.observed_at)
        ), warnings

    def _verified(self, payload: dict[str, Any]) -> bool:
        content_hash = payload.get("content_hash")
        if payload.get("schema_version") != "market-archive-v2" or not content_hash:
            return False
        core = {"run": payload.get("run"), "bars": payload.get("bars", [])}
        content = json.dumps(
            core, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(content).hexdigest() == content_hash


def _market_bar(value: dict[str, Any]) -> MarketBar:
    data = dict(value)
    for key in ("observed_at", "available_at", "ingested_at"):
        if data.get(key):
            data[key] = datetime.fromisoformat(data[key])
    return MarketBar(**data)


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
