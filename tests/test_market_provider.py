from datetime import datetime, timezone

import pytest

from app.market.adapters import (
    EastMoneyBridgeMarketDataProvider,
    EastMoneyMarketDataProvider,
    FallbackMarketDataProvider,
)
from app.market.provider import (
    InMemoryMarketDataProvider,
    MarketBar,
    MarketInstrument,
    UnavailableMarketDataProvider,
)

AS_OF = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)


def _bar(available_at: datetime = AS_OF) -> MarketBar:
    return MarketBar(
        instrument_id="cn:000001",
        market="cn",
        symbol="000001",
        interval="1d",
        observed_at=datetime(2026, 8, 17, 7, tzinfo=timezone.utc),
        open=100,
        high=105,
        low=99,
        close=104,
        source="test",
        available_at=available_at,
    )


def test_in_memory_provider_rejects_future_bars() -> None:
    provider = InMemoryMarketDataProvider([_bar(AS_OF.replace(day=19))])
    with pytest.raises(ValueError, match="newer than as_of"):
        provider.get_bars(
            instrument_ids=["cn:000001"],
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 31, tzinfo=timezone.utc),
            interval="1d",
            as_of=AS_OF,
            limit=100,
        )


def test_eastmoney_adapter_normalizes_snapshot_and_daily_bar() -> None:
    instrument = MarketInstrument(
        id="cn:000001",
        market="cn",
        symbol="000001",
        name="平安银行",
        instrument_type="stock",
        provider_symbols={"eastmoney": "0.000001"},
    )

    def transport(url: str, params: dict[str, object]) -> dict[str, object]:
        if "stock/get" in url:
            return {"data": {"f43": 10400, "f169": 100, "f170": 0.96, "f47": 1200, "f48": 3400}}
        return {"data": {"klines": ["2026-08-17,100,104,105,99,1200,3400"]}}

    provider = EastMoneyMarketDataProvider({instrument.id: instrument}, request_json=transport)
    snapshot = provider.get_snapshots(instrument_ids=[instrument.id], as_of=AS_OF)
    bars = provider.get_bars(
        instrument_ids=[instrument.id],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 31, tzinfo=timezone.utc),
        interval="1d",
        as_of=AS_OF,
        limit=100,
    )
    assert snapshot.snapshots[0].last == 104
    assert snapshot.snapshots[0].change_percent == pytest.approx(0.0096)
    assert bars.bars[0].close == 104
    assert bars.bars[0].source == "eastmoney"


def test_fallback_records_primary_degradation() -> None:
    primary = UnavailableMarketDataProvider()
    fallback = InMemoryMarketDataProvider([_bar()])
    result = FallbackMarketDataProvider(primary, fallback).get_bars(
        instrument_ids=["cn:000001"],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 31, tzinfo=timezone.utc),
        interval="1d",
        as_of=AS_OF,
        limit=100,
    )
    assert result.status == "ok"
    assert result.bars
    assert "primary_unavailable" in result.warnings


def test_bridge_adapter_reads_quote_snapshot_contract() -> None:
    instrument = MarketInstrument(
        id="cn:000001",
        market="cn",
        symbol="000001",
        name="上证指数",
        instrument_type="index",
        provider_symbols={"eastmoney": "1.000001"},
    )

    def transport(url: str, params: dict[str, object]) -> dict[str, object]:
        assert url.endswith("/api/v1/market/quote/1.000001")
        assert params["allow_stale"] == "true"
        return {
            "items": [
                {
                    "price": 3200.5,
                    "change": 12.5,
                    "change_percent": 0.39,
                    "volume": 1000,
                    "amount": 2000,
                    "captured_at": "2026-08-18T07:30:00Z",
                }
            ]
        }

    provider = EastMoneyBridgeMarketDataProvider(
        {instrument.id: instrument}, request_json=transport
    )
    result = provider.get_snapshots(
        instrument_ids=[instrument.id], as_of=AS_OF
    )

    assert result.status == "ok"
    assert result.snapshots[0].last == 3200.5
    assert result.snapshots[0].change_percent == pytest.approx(0.0039)
    assert result.snapshots[0].source == "eastmoney-browser-bridge"


def test_fallback_selects_more_complete_daily_history_per_instrument() -> None:
    def bars(count: int, source: str) -> list[MarketBar]:
        return [
            MarketBar(
                **{
                    **_bar().__dict__,
                    "observed_at": datetime(2026, 8, day, 7, tzinfo=timezone.utc),
                    "source": source,
                }
            )
            for day in range(1, count + 1)
        ]

    primary = InMemoryMarketDataProvider(bars(2, "primary"))
    fallback = InMemoryMarketDataProvider(bars(10, "fallback"))
    result = FallbackMarketDataProvider(primary, fallback).get_bars(
        instrument_ids=["cn:000001"],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, tzinfo=timezone.utc),
        interval="1d",
        as_of=AS_OF,
        limit=100,
    )

    assert len(result.bars) == 10
    assert {bar.source for bar in result.bars} == {"fallback"}
    assert "cn:000001:fallback_selected" in result.warnings


def test_fallback_drops_series_with_mixed_adjustment_conventions() -> None:
    mixed = [
        MarketBar(
            **{
                **_bar().__dict__,
                "observed_at": datetime(2026, 8, day, 7, tzinfo=timezone.utc),
                "adjustment": "qfq" if day % 2 else "none",
            }
        )
        for day in range(1, 11)
    ]
    result = FallbackMarketDataProvider(
        InMemoryMarketDataProvider(mixed), UnavailableMarketDataProvider()
    ).get_bars(
        instrument_ids=["cn:000001"],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, tzinfo=timezone.utc),
        interval="1d",
        as_of=AS_OF,
        limit=100,
    )

    assert result.bars == []
    assert result.status == "degraded"
    assert "cn:000001:mixed_adjustment:none/qfq" in result.warnings


def test_eastmoney_network_failure_degrades_without_raising() -> None:
    def failing_transport(url: str, params: dict[str, object]) -> dict[str, object]:
        raise TimeoutError("upstream timeout")

    provider = EastMoneyMarketDataProvider(request_json=failing_transport)
    result = provider.get_snapshots(instrument_ids=["cn:000001"], as_of=AS_OF)
    assert result.status == "degraded"
    assert result.warnings == ["cn:000001:provider_error"]


def test_eastmoney_bridge_normalizes_kline_and_preserves_stale_warning() -> None:
    instrument = MarketInstrument(
        id="cn:index:000300", market="cn", symbol="000300", name="沪深300",
        instrument_type="index", provider_symbols={"eastmoney": "1.000300"},
    )

    def transport(url: str, params: dict[str, object]) -> dict[str, object]:
        assert url.endswith("/api/v1/market/kline/1.000300")
        assert params["allow_stale"] == "true"
        return {
            "source": "browser-ingest", "fresh": False, "stale": True,
            "items": [{
                "secid": "1.000300", "date": "2026-08-17", "open": "4000",
                "close": "4020", "high": "4030", "low": "3990",
                "volume": "1200", "amount": "3400", "captured_at": "2026-08-18T08:00:00+00:00",
            }],
        }

    provider = EastMoneyBridgeMarketDataProvider(
        {instrument.id: instrument}, request_json=transport,
    )
    result = provider.get_bars(
        instrument_ids=[instrument.id],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 31, tzinfo=timezone.utc),
        interval="1d", as_of=AS_OF, limit=100,
    )
    assert result.status == "ok"
    assert result.bars[0].close == 4020
    assert result.bars[0].source == "eastmoney-browser-bridge"
    assert result.warnings == ["cn:index:000300:bridge_stale_data"]


def test_eastmoney_localizes_intraday_bars_from_exchange_time_to_utc() -> None:
    instrument = MarketInstrument(
        id="cn:index:000300", market="cn", symbol="000300", name="沪深300",
        instrument_type="index", provider_symbols={"eastmoney": "1.000300"},
    )

    def transport(url: str, params: dict[str, object]) -> dict[str, object]:
        assert params["klt"] == "5"
        return {"data": {"klines": ["2026-08-17 14:55,4000,4020,4030,3990,1200,3400"]}}

    provider = EastMoneyMarketDataProvider({instrument.id: instrument}, request_json=transport)
    result = provider.get_bars(
        instrument_ids=[instrument.id],
        start=datetime(2026, 8, 17, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, tzinfo=timezone.utc),
        interval="5m",
        as_of=AS_OF,
        limit=100,
    )

    # 14:55 Asia/Shanghai is 06:55Z.  Labeling the wall clock as UTC would place
    # the bar at 14:55Z, eight hours after the session actually closed.
    assert result.bars[0].observed_at == datetime(2026, 8, 17, 6, 55, tzinfo=timezone.utc)


def test_us_intraday_bars_use_exchange_timezone() -> None:
    instrument = MarketInstrument(
        id="us:index:SPX", market="us", symbol="SPX", name="S&P 500",
        instrument_type="index", provider_symbols={"eastmoney": "100.SPX"},
    )

    def transport(url: str, params: dict[str, object]) -> dict[str, object]:
        return {"data": {"klines": ["2026-08-17 09:35,4000,4020,4030,3990,1200,3400"]}}

    provider = EastMoneyMarketDataProvider({instrument.id: instrument}, request_json=transport)
    result = provider.get_bars(
        instrument_ids=[instrument.id],
        start=datetime(2026, 8, 17, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, tzinfo=timezone.utc),
        interval="5m",
        as_of=AS_OF,
        limit=100,
    )

    # 09:35 America/New_York during DST is 13:35Z.
    assert result.bars[0].observed_at == datetime(2026, 8, 17, 13, 35, tzinfo=timezone.utc)


def test_daily_bars_keep_utc_midnight_trading_date_marker() -> None:
    instrument = MarketInstrument(
        id="cn:index:000300", market="cn", symbol="000300", name="沪深300",
        instrument_type="index", provider_symbols={"eastmoney": "1.000300"},
    )

    def transport(url: str, params: dict[str, object]) -> dict[str, object]:
        return {"data": {"klines": ["2026-08-17,4000,4020,4030,3990,1200,3400"]}}

    provider = EastMoneyMarketDataProvider({instrument.id: instrument}, request_json=transport)
    result = provider.get_bars(
        instrument_ids=[instrument.id],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 31, tzinfo=timezone.utc),
        interval="1d",
        as_of=AS_OF,
        limit=100,
    )

    assert result.bars[0].observed_at == datetime(2026, 8, 17, tzinfo=timezone.utc)


def test_bridge_localizes_intraday_bars() -> None:
    instrument = MarketInstrument(
        id="cn:index:000300", market="cn", symbol="000300", name="沪深300",
        instrument_type="index", provider_symbols={"eastmoney": "1.000300"},
    )

    def transport(url: str, params: dict[str, object]) -> dict[str, object]:
        return {
            "items": [{
                "secid": "1.000300", "time": "2026-08-17 10:30", "open": "4000",
                "close": "4020", "high": "4030", "low": "3990",
            }],
        }

    provider = EastMoneyBridgeMarketDataProvider(
        {instrument.id: instrument}, request_json=transport,
    )
    result = provider.get_bars(
        instrument_ids=[instrument.id],
        start=datetime(2026, 8, 17, tzinfo=timezone.utc),
        end=datetime(2026, 8, 18, tzinfo=timezone.utc),
        interval="5m", as_of=AS_OF, limit=100,
    )

    assert result.bars[0].observed_at == datetime(2026, 8, 17, 2, 30, tzinfo=timezone.utc)


def test_market_bar_rejects_inconsistent_ohlc() -> None:
    with pytest.raises(ValueError, match="high/low"):
        MarketBar(
            instrument_id="cn:000001",
            market="cn",
            symbol="000001",
            interval="1d",
            observed_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
            open=100,
            high=99,
            low=98,
            close=101,
        )
