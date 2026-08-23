"""Versioned market reference data used before the catalog database is enabled."""

from __future__ import annotations

from dataclasses import replace

from app.market.provider import MarketInstrument

DEFAULT_INSTRUMENTS = (
    MarketInstrument(
        id="cn:index:000001", market="cn", symbol="000001", name="上证指数",
        instrument_type="index", exchange="SSE", currency="CNY", timezone="Asia/Shanghai",
        provider_symbols={"eastmoney": "1.000001"},
    ),
    MarketInstrument(
        id="cn:index:000300", market="cn", symbol="000300", name="沪深300",
        instrument_type="index", exchange="CSI", currency="CNY", timezone="Asia/Shanghai",
        provider_symbols={"eastmoney": "1.000300"},
    ),
    MarketInstrument(
        id="cn:etf:510300", market="cn", symbol="510300", name="沪深300ETF",
        instrument_type="etf", exchange="SSE", currency="CNY", timezone="Asia/Shanghai",
        provider_symbols={"eastmoney": "1.510300"},
    ),
    MarketInstrument(
        id="cn:etf:512800", market="cn", symbol="512800", name="银行ETF",
        instrument_type="etf", exchange="SSE", currency="CNY", timezone="Asia/Shanghai",
        sector_code="cn-banks", sector_name="银行",
        provider_symbols={"eastmoney": "1.512800"},
    ),
    MarketInstrument(
        id="cn:etf:512200", market="cn", symbol="512200", name="房地产ETF",
        instrument_type="etf", exchange="SSE", currency="CNY", timezone="Asia/Shanghai",
        sector_code="cn-real-estate", sector_name="房地产",
        provider_symbols={"eastmoney": "1.512200"},
    ),
    MarketInstrument(
        id="hk:index:HSI", market="hk", symbol="HSI", name="恒生指数",
        instrument_type="index", exchange="HKEX", currency="HKD", timezone="Asia/Hong_Kong",
        provider_symbols={"eastmoney": "100.HSI"},
    ),
    MarketInstrument(
        id="us:index:SPX", market="us", symbol="SPX", name="标普500",
        instrument_type="index", exchange="CBOE", currency="USD", timezone="America/New_York",
        provider_symbols={"eastmoney": "100.SPX"},
    ),
    MarketInstrument(
        id="us:etf:SPY", market="us", symbol="SPY", name="SPDR标普500ETF",
        instrument_type="etf", exchange="NYSE", currency="USD", timezone="America/New_York",
        provider_symbols={"eastmoney": "100.SPY"},
    ),
)


class MarketInstrumentCatalog:
    def __init__(self, instruments: tuple[MarketInstrument, ...] = DEFAULT_INSTRUMENTS) -> None:
        self._items = {item.id: item for item in instruments}

    def list(
        self,
        *,
        market: str | None = None,
        instrument_type: str | None = None,
    ) -> list[MarketInstrument]:
        return sorted(
            [
                item for item in self._items.values()
                if (market is None or item.market == market)
                and (instrument_type is None or item.instrument_type == instrument_type)
            ],
            key=lambda item: (item.market, item.instrument_type, item.symbol),
        )

    def get(self, instrument_id: str) -> MarketInstrument | None:
        return self._items.get(instrument_id)

    def as_mapping(self) -> dict[str, MarketInstrument]:
        return dict(self._items)

    def register(self, instrument: MarketInstrument) -> None:
        current = self._items.get(instrument.id)
        if current and current != instrument:
            raise ValueError(f"market instrument already registered: {instrument.id}")
        self._items[instrument.id] = replace(instrument)
