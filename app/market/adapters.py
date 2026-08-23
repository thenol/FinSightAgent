"""Research market-data adapters inspired by the stock project.

The adapters return the platform contracts and never leak provider-specific
DataFrames or response dictionaries into analysis code.  Network access is
kept behind ``request_json`` so tests can use a deterministic transport.
"""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from app.market.provider import (
    INTERVALS,
    MARKET_CODES,
    MARKET_TIMEZONES,
    MarketBar,
    MarketDataCapability,
    MarketDataProvider,
    MarketDataResult,
    MarketInstrument,
    MarketSnapshot,
    UnavailableMarketDataProvider,
)

logger = logging.getLogger(__name__)
JsonTransport = Callable[[str, dict[str, Any]], dict[str, Any]]


def _as_float(value: Any, *, scale: float = 1.0) -> float | None:
    if value is None or value in {"", "-"}:
        return None
    try:
        return float(value) / scale
    except (TypeError, ValueError):
        return None


def _market_from_instrument(instrument: MarketInstrument) -> str:
    if instrument.market not in MARKET_CODES:
        raise ValueError(f"unsupported market: {instrument.market}")
    return instrument.market


class EastMoneyMarketDataProvider:
    """东方财富 JSON adapter with explicit provider metadata.

    The stock project uses the same push2/push2his families.  This adapter
    normalizes their scaled fields and keeps the source URL out of domain
    objects.  It is intentionally constructed with an instrument catalog so
    provider symbol mapping is versioned by the platform rather than guessed.
    """

    provider_name = "eastmoney"
    quote_url = "https://push2.eastmoney.com/api/qt/stock/get"
    kline_url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    def __init__(
        self,
        instruments: dict[str, MarketInstrument] | None = None,
        *,
        timeout_seconds: float = 15.0,
        request_json: JsonTransport | None = None,
    ) -> None:
        self.instruments = instruments or {}
        self.timeout_seconds = timeout_seconds
        self._request_json = request_json or self._request
        self._last_success_at: datetime | None = None
        self._capability = MarketDataCapability(
            provider=self.provider_name,
            status="available",
            supported_markets=list(MARKET_CODES),
            supported_intervals=list(INTERVALS),
        )

    @property
    def capability(self) -> MarketDataCapability:
        return replace(self._capability, last_success_at=self._last_success_at)

    def _request(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        # The bridge is a local service.  Do not send loopback requests through
        # HTTP(S)_PROXY inherited from the developer shell.
        response = httpx.get(
            url, params=params, timeout=self.timeout_seconds, trust_env=False
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("eastmoney response is not an object")
        self._last_success_at = datetime.now(timezone.utc)
        return payload

    def _instrument(self, instrument_id: str) -> MarketInstrument:
        instrument = self.instruments.get(instrument_id)
        if instrument is None:
            # Local research queries may use the canonical ``market:symbol``
            # form before the master-data table is populated.  Keep this
            # parser deliberately narrow; production writes still require a
            # registered instrument and versioned provider mapping.
            try:
                market, symbol = instrument_id.split(":", 1)
            except ValueError as exc:
                raise ValueError(f"market instrument is not registered: {instrument_id}") from exc
            if market not in MARKET_CODES or not symbol:
                raise ValueError(f"market instrument is not registered: {instrument_id}")
            instrument = MarketInstrument(
                id=instrument_id,
                market=market,
                symbol=symbol,
                name=symbol,
                instrument_type="index",
                provider_symbols={
                    self.provider_name: self._default_provider_symbol(market, symbol)
                },
            )
        return instrument

    @staticmethod
    def _default_provider_symbol(market: str, symbol: str) -> str:
        if market == "cn":
            prefix = "1" if symbol.startswith(("5", "6", "68", "9")) else "0"
        else:
            prefix = {"hk": "116", "us": "100"}[market]
        return f"{prefix}.{symbol}"

    def _secid(self, instrument: MarketInstrument) -> str:
        mapped = instrument.provider_symbols.get(self.provider_name)
        if mapped:
            return mapped
        return self._default_provider_symbol(instrument.market, instrument.symbol)

    def get_snapshots(self, *, instrument_ids, as_of) -> MarketDataResult:
        snapshots: list[MarketSnapshot] = []
        warnings: list[str] = []
        for instrument_id in instrument_ids:
            instrument = self._instrument(instrument_id)
            try:
                payload = self._request_json(
                    self.quote_url,
                    {"secid": self._secid(instrument), "fields": "f43,f169,f170,f47,f48,f58"},
                )
            except Exception as exc:
                logger.warning("eastmoney snapshot failed for %s: %s", instrument_id, exc)
                warnings.append(f"{instrument_id}:provider_error")
                continue
            data = payload.get("data") or {}
            observed_at = datetime.now(timezone.utc)
            last = _as_float(data.get("f43"), scale=100)
            if last is None:
                warnings.append(f"{instrument_id}:missing_last")
                continue
            snapshots.append(MarketSnapshot(
                instrument_id=instrument.id,
                market=_market_from_instrument(instrument),
                symbol=instrument.symbol,
                observed_at=observed_at,
                last=last,
                change=_as_float(data.get("f169"), scale=100),
                change_percent=_as_float(data.get("f170"), scale=100),
                volume=_as_float(data.get("f47")),
                amount=_as_float(data.get("f48")),
                source=self.provider_name,
                available_at=observed_at,
            ))
        return MarketDataResult(
            status="ok" if snapshots else "degraded",
            snapshots=snapshots,
            capability=self.capability,
            warnings=warnings,
        )

    def get_bars(self, *, instrument_ids, start, end, interval, as_of, limit) -> MarketDataResult:
        if interval not in INTERVALS:
            raise ValueError(f"unsupported market interval: {interval}")
        # EastMoney klt=5 is the normalized five-minute contract; klt=101 is daily.
        klt = "5" if interval == "5m" else "101"
        bars: list[MarketBar] = []
        warnings: list[str] = []
        for instrument_id in instrument_ids:
            instrument = self._instrument(instrument_id)
            try:
                payload = self._request_json(
                    self.kline_url,
                    {
                        "secid": self._secid(instrument),
                        "klt": klt,
                        "fqt": "1" if interval == "1d" else "0",
                        "lmt": str(limit),
                        "beg": start.astimezone(timezone.utc).strftime("%Y%m%d"),
                        "end": end.astimezone(timezone.utc).strftime("%Y%m%d"),
                        "fields1": "f1,f2,f3,f4",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    },
                )
            except Exception as exc:
                logger.warning("eastmoney bars failed for %s: %s", instrument_id, exc)
                warnings.append(f"{instrument_id}:provider_error")
                continue
            rows = ((payload.get("data") or {}).get("klines") or [])
            if not rows:
                warnings.append(f"{instrument_id}:empty_bars")
                continue
            for row in rows[-limit:]:
                fields = row.split(",") if isinstance(row, str) else row
                if len(fields) < 7:
                    continue
                observed_at = _parse_provider_time(
                    str(fields[0]), interval, _market_from_instrument(instrument)
                )
                if observed_at is None or not start <= observed_at <= end:
                    continue
                open_value = _as_float(fields[1])
                close_value = _as_float(fields[2])
                high_value = _as_float(fields[3])
                low_value = _as_float(fields[4])
                if None in {open_value, close_value, high_value, low_value}:
                    continue
                bars.append(MarketBar(
                    instrument_id=instrument.id,
                    market=_market_from_instrument(instrument),
                    symbol=instrument.symbol,
                    interval=interval,
                    observed_at=observed_at,
                    open=open_value,
                    close=close_value,
                    high=high_value,
                    low=low_value,
                    volume=_as_float(fields[5]),
                    amount=_as_float(fields[6]),
                    adjustment="qfq" if interval == "1d" else "none",
                    source=self.provider_name,
                    available_at=as_of,
                    ingested_at=datetime.now(timezone.utc),
                ))
        bars.sort(key=lambda item: (item.instrument_id, item.observed_at))
        return MarketDataResult(
            status="ok" if bars else "degraded",
            bars=_limit_bars_per_instrument(bars, limit),
            capability=self.capability,
            warnings=warnings,
        )

    def get_calendar(self, *, market, start, end, as_of) -> MarketDataResult:
        # Calendar ownership remains with the platform's normalized calendar
        # service; EastMoney does not become the source of truth for holidays.
        return MarketDataResult(
            status="unsupported",
            capability=MarketDataCapability(
                provider=self.provider_name,
                status="degraded",
                supported_markets=list(MARKET_CODES),
                supported_intervals=list(INTERVALS),
                reason="calendar_requires_platform_source",
            ),
            warnings=[f"calendar_not_supported:{market}:{start}:{end}:{as_of.isoformat()}"],
        )

    def query(self, *, security_ids, start, end, as_of, limit) -> MarketDataResult:
        if start is None or end is None or as_of is None:
            return MarketDataResult(
                status="invalid",
                capability=self.capability,
                warnings=["start_end_as_of_required"],
            )
        return self.get_bars(
            instrument_ids=security_ids,
            start=start,
            end=end,
            interval="1d",
            as_of=as_of,
            limit=limit,
        )


class EastMoneyBridgeMarketDataProvider:
    """Provider for the local eastmoney-api-bridge normalized API."""

    def __init__(
        self,
        instruments: dict[str, MarketInstrument] | None = None,
        *,
        base_url: str = "http://127.0.0.1:8765",
        timeout_seconds: float = 5.0,
        request_json: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.instruments = instruments or {}
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._request_json = request_json or self._request
        self._last_success_at: datetime | None = None
        self._capability = MarketDataCapability(
            provider="eastmoney-bridge", status="available",
            supported_markets=list(MARKET_CODES), supported_intervals=list(INTERVALS),
            reason="local_browser_bridge",
        )

    @property
    def capability(self) -> MarketDataCapability:
        return replace(self._capability, last_success_at=self._last_success_at)

    def _request(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = httpx.get(
            url, params=params, timeout=self.timeout_seconds, trust_env=False
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("eastmoney bridge response is not an object")
        self._last_success_at = datetime.now(timezone.utc)
        return payload

    def _secid(self, instrument_id: str) -> str:
        instrument = self.instruments.get(instrument_id)
        if instrument is None:
            raise ValueError(f"market instrument is not registered: {instrument_id}")
        secid = instrument.provider_symbols.get("eastmoney")
        if not secid:
            raise ValueError(f"eastmoney secid is not configured: {instrument_id}")
        return secid

    def get_bars(self, *, instrument_ids, start, end, interval, as_of, limit):
        if interval not in INTERVALS:
            raise ValueError(f"unsupported market interval: {interval}")
        bars: list[MarketBar] = []
        warnings: list[str] = []
        endpoint = "trends" if interval == "5m" else "kline"
        for instrument_id in instrument_ids:
            try:
                payload = self._request_json(
                    f"{self.base_url}/api/v1/market/{endpoint}/{self._secid(instrument_id)}",
                    {"allow_stale": "true", "limit": limit},
                )
                if payload.get("stale"):
                    warnings.append(f"{instrument_id}:bridge_stale_data")
                instrument = self.instruments[instrument_id]
                for item in payload.get("items", []):
                    bar = self._bar_from_item(item, instrument, interval, as_of)
                    if bar is not None and start <= bar.observed_at <= end:
                        bars.append(bar)
            except Exception as exc:
                logger.warning("eastmoney bridge bars failed for %s: %s", instrument_id, exc)
                warnings.append(f"{instrument_id}:bridge_provider_error")
        bars.sort(key=lambda item: (item.instrument_id, item.observed_at))
        return MarketDataResult(
            status="ok" if bars else "degraded",
            bars=_limit_bars_per_instrument(bars, limit),
            capability=self.capability, warnings=warnings,
        )

    def query(self, *, security_ids, start, end, as_of, limit):
        if start is None or end is None or as_of is None:
            return MarketDataResult(status="invalid", capability=self.capability,
                                    warnings=["start_end_as_of_required"])
        return self.get_bars(
            instrument_ids=security_ids, start=start, end=end, interval="1d",
            as_of=as_of, limit=limit,
        )

    def get_snapshots(self, *, instrument_ids, as_of):
        return MarketDataResult(
            status="unsupported", capability=self.capability,
            warnings=["bridge_snapshot_not_implemented"],
        )

    def get_calendar(self, *, market, start, end, as_of):
        return MarketDataResult(
            status="unsupported", capability=self.capability,
            warnings=["bridge_calendar_not_supported"],
        )

    @staticmethod
    def _bar_from_item(
        item: dict[str, Any], instrument: MarketInstrument, interval: str, as_of: datetime
    ):
        time_value = item.get("time") or item.get("date")
        observed_at = (
            _parse_provider_time(str(time_value), interval, instrument.market)
            if time_value
            else None
        )
        if observed_at is None:
            return None
        values = {key: _as_float(item.get(key)) for key in ("open", "close", "high", "low")}
        if any(value is None for value in values.values()):
            return None
        captured_at = item.get("captured_at")
        available_at = _parse_iso_timestamp(captured_at) or as_of
        return MarketBar(
            instrument_id=instrument.id, market=instrument.market, symbol=instrument.symbol,
            interval=interval, observed_at=observed_at, open=values["open"],
            close=values["close"], high=values["high"], low=values["low"],
            volume=_as_float(item.get("volume")), amount=_as_float(item.get("amount")),
            vwap=_as_float(item.get("average")), adjustment="none",
            source="eastmoney-browser-bridge", available_at=available_at,
            ingested_at=datetime.now(timezone.utc),
        )


def _parse_provider_time(value: str, interval: str, market: str) -> datetime | None:
    """Convert an exchange-local provider timestamp to UTC.

    Daily bars keep UTC midnight as a pure trading-date marker; they identify a
    session, not an instant.  Intraday bars are real instants and must be
    localized to the exchange timezone before conversion, otherwise a Beijing
    wall clock read as UTC lands 8 hours in the future and makes every staleness
    and ``as_of`` comparison meaningless.
    """

    try:
        if interval == "1d":
            return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        local = datetime.strptime(value[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    timezone_name = MARKET_TIMEZONES.get(market)
    if timezone_name is None:
        raise ValueError(f"unsupported market: {market}")
    return local.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _limit_bars_per_instrument(bars: list[MarketBar], limit: int) -> list[MarketBar]:
    grouped = _bars_by_instrument(bars)
    selected = [bar for values in grouped.values() for bar in values[-limit:]]
    return sorted(selected, key=lambda item: (item.instrument_id, item.observed_at))


def _bars_by_instrument(bars: list[MarketBar]) -> dict[str, list[MarketBar]]:
    grouped: dict[str, list[MarketBar]] = {}
    for bar in bars:
        grouped.setdefault(bar.instrument_id, []).append(bar)
    for values in grouped.values():
        values.sort(key=lambda item: item.observed_at)
    return grouped


def _expected_bar_count(start: datetime, end: datetime, interval: str, limit: int) -> int:
    if interval != "1d":
        return 1
    total_days = max(0, (end.date() - start.date()).days) + 1
    weekdays = sum(
        (start.date().toordinal() + offset) % 7 not in {0, 6}
        for offset in range(total_days)
    )
    # Completeness routing is deliberately tolerant of exchange holidays. The
    # formal calendar still owns downstream quality and exact coverage checks.
    return max(1, min(limit, math.ceil(weekdays * 0.8)))


class FallbackMarketDataProvider:
    """Primary/fallback routing with explicit provenance and degradation."""

    def __init__(self, primary: MarketDataProvider, fallback: MarketDataProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    @property
    def capability(self) -> MarketDataCapability:
        return MarketDataCapability(
            provider=f"{self.primary.capability.provider}+{self.fallback.capability.provider}",
            status=(
                "available"
                if self.primary.capability.status == "available"
                else self.fallback.capability.status
            ),
            supported_markets=sorted(
                set(
                    self.primary.capability.supported_markets
                    + self.fallback.capability.supported_markets
                )
            ),
            supported_intervals=sorted(
                set(
                    self.primary.capability.supported_intervals
                    + self.fallback.capability.supported_intervals
                )
            ),
            reason="primary_with_fallback",
        )

    def _run(self, method: str, **kwargs: Any) -> MarketDataResult:
        try:
            result = getattr(self.primary, method)(**kwargs)
            has_data = result.bars or result.snapshots or result.calendar or result.observations
            if result.status in {"ok", "available"} and has_data:
                return result
            primary_warning = f"primary_{result.status}"
        except Exception as exc:  # provider failures must be visible, not fatal to research queries
            logger.warning("primary market provider failed: %s", exc)
            primary_warning = "primary_exception"
        fallback_result = getattr(self.fallback, method)(**kwargs)
        return MarketDataResult(
            **{**fallback_result.__dict__, "warnings": [primary_warning, *fallback_result.warnings]}
        )

    def query(self, **kwargs: Any) -> MarketDataResult:
        return self._run("query", **kwargs)

    def get_bars(self, **kwargs: Any) -> MarketDataResult:
        try:
            primary_result = self.primary.get_bars(**kwargs)
        except Exception as exc:  # provider failure remains visible while fallback proceeds
            logger.warning("primary market provider failed: %s", exc)
            primary_result = MarketDataResult(
                status="unavailable",
                capability=self.primary.capability,
                warnings=["primary_exception"],
            )
        instrument_ids = list(kwargs.get("instrument_ids") or [])
        interval = kwargs.get("interval")
        limit = int(kwargs.get("limit") or 0)
        expected = _expected_bar_count(kwargs["start"], kwargs["end"], interval, limit)
        primary_by_id = _bars_by_instrument(primary_result.bars)
        incomplete = [
            instrument_id
            for instrument_id in instrument_ids
            if len(primary_by_id.get(instrument_id, ())) < expected
        ]
        if not incomplete:
            return primary_result

        fallback_kwargs = {**kwargs, "instrument_ids": incomplete}
        try:
            fallback_result = self.fallback.get_bars(**fallback_kwargs)
        except Exception as exc:
            logger.warning("fallback market provider failed: %s", exc)
            fallback_result = MarketDataResult(
                status="unavailable",
                capability=self.fallback.capability,
                warnings=["fallback_exception"],
            )
        fallback_by_id = _bars_by_instrument(fallback_result.bars)
        selected: list[MarketBar] = []
        warnings = list(primary_result.warnings)
        if primary_result.status not in {"ok", "available"}:
            warnings.insert(0, f"primary_{primary_result.status}")
        for instrument_id in instrument_ids:
            primary_bars = primary_by_id.get(instrument_id, [])
            fallback_bars = fallback_by_id.get(instrument_id, [])
            if instrument_id in incomplete:
                warnings.append(
                    f"{instrument_id}:primary_incomplete:{len(primary_bars)}/{expected}"
                )
            if len(fallback_bars) > len(primary_bars):
                selected.extend(fallback_bars)
                warnings.append(f"{instrument_id}:fallback_selected")
            else:
                selected.extend(primary_bars)
        warnings.extend(fallback_result.warnings)
        selected = _limit_bars_per_instrument(selected, limit)
        available_ids = {bar.instrument_id for bar in selected}
        return MarketDataResult(
            status="ok" if set(instrument_ids) <= available_ids else "degraded",
            bars=selected,
            capability=self.capability,
            warnings=list(dict.fromkeys(warnings)),
        )

    def get_snapshots(self, **kwargs: Any) -> MarketDataResult:
        return self._run("get_snapshots", **kwargs)

    def get_calendar(self, **kwargs: Any) -> MarketDataResult:
        return self._run("get_calendar", **kwargs)


class AkShareMarketDataProvider(UnavailableMarketDataProvider):
    """Optional AKShare adapter for CN daily and five-minute history.

    AKShare remains optional because it brings a broad pandas dependency tree.
    When installed, this adapter uses the documented ``index_zh_a_hist`` /
    ``index_zh_a_hist_min_em`` and ``fund_etf_hist_em`` /
    ``fund_etf_hist_min_em`` interfaces, while keeping DataFrames inside the
    adapter boundary.
    """

    def __init__(self) -> None:
        try:
            import akshare as ak  # type: ignore[import-not-found]
        except ImportError:
            self._ak = None
            reason = "optional_akshare_adapter_not_installed"
            status = "unavailable"
        else:
            self._ak = ak
            reason = None
            status = "available"
        self._last_success_at: datetime | None = None
        self._capability = MarketDataCapability(
            provider="akshare", status=status, supported_markets=["cn"],
            supported_intervals=["1d", "5m"], reason=reason,
        )

    @property
    def capability(self) -> MarketDataCapability:
        return replace(self._capability, last_success_at=self._last_success_at)

    def get_bars(self, *, instrument_ids, start, end, interval, as_of, limit):
        if self._ak is None:
            return MarketDataResult(status="unavailable", capability=self.capability,
                                    warnings=["akshare_not_installed"])
        if interval not in INTERVALS:
            raise ValueError(f"unsupported market interval: {interval}")
        bars: list[MarketBar] = []
        warnings: list[str] = []
        for instrument_id in instrument_ids:
            try:
                market, kind, symbol = instrument_id.split(":", 2)
                if market != "cn":
                    raise ValueError("akshare supports cn only")
                frame = self._fetch_frame(symbol, kind, start, end, interval)
                self._last_success_at = datetime.now(timezone.utc)
                bars.extend(
                    self._convert_frame(frame, instrument_id, kind, symbol, interval, as_of)
                )
            except Exception as exc:
                logger.warning("akshare bars failed for %s: %s", instrument_id, exc)
                warnings.append(f"{instrument_id}:provider_error")
        bars = [bar for bar in bars if start <= bar.observed_at <= end]
        bars.sort(key=lambda item: (item.instrument_id, item.observed_at))
        return MarketDataResult(
            status="ok" if bars else "degraded",
            bars=_limit_bars_per_instrument(bars, limit),
            capability=self.capability, warnings=warnings,
        )

    def _fetch_frame(self, symbol, kind, start, end, interval):
        start_day = start.strftime("%Y%m%d")
        end_day = end.strftime("%Y%m%d")
        if interval == "1d":
            if kind == "index":
                return self._ak.index_zh_a_hist(
                    symbol=symbol, period="daily", start_date=start_day, end_date=end_day
                )
            if kind == "etf":
                return self._ak.fund_etf_hist_em(
                    symbol=symbol, period="daily", start_date=start_day,
                    end_date=end_day, adjust=""
                )
        start_time = start.strftime("%Y-%m-%d %H:%M:%S")
        end_time = end.strftime("%Y-%m-%d %H:%M:%S")
        if kind == "index":
            return self._ak.index_zh_a_hist_min_em(
                symbol=symbol, period="5", start_date=start_time, end_date=end_time
            )
        if kind == "etf":
            return self._ak.fund_etf_hist_min_em(
                symbol=symbol, period="5", start_date=start_time,
                end_date=end_time, adjust=""
            )
        raise ValueError(f"unsupported akshare instrument type: {kind}")

    @staticmethod
    def _convert_frame(frame, instrument_id, kind, symbol, interval, as_of):
        if frame is None or frame.empty:
            return []
        columns = {str(column): column for column in frame.columns}
        time_column = columns.get("日期") or columns.get("时间")
        if time_column is None:
            raise ValueError("akshare response missing time column")
        result = []
        for _, row in frame.iterrows():
            # AKShare only serves CN instruments; ``get_bars`` rejects others.
            observed_at = _parse_provider_time(str(row[time_column]), interval, "cn")
            if observed_at is None:
                continue
            value_names = ("开盘", "收盘", "最高", "最低", "成交量", "成交额")
            values = {
                name: _as_float(row[columns[name]]) if name in columns else None
                for name in value_names
            }
            open_value, close_value = values["开盘"], values["收盘"]
            high_value, low_value = values["最高"], values["最低"]
            if None in {open_value, close_value, high_value, low_value}:
                continue
            result.append(MarketBar(
                instrument_id=instrument_id, market="cn", symbol=symbol, interval=interval,
                observed_at=observed_at, open=open_value, close=close_value,
                high=high_value, low=low_value, volume=values["成交量"], amount=values["成交额"],
                adjustment="none", source="akshare", available_at=as_of,
                ingested_at=datetime.now(timezone.utc),
            ))
        return result
