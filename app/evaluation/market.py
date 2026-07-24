"""可替换行情提供方与确定性市场评估 Stub。

Stub 只用于验证 1/3/5/20 交易日窗口、防未来泄漏和异常收益计算；其输出不是
真实市场数据，不能作为 MVP 的真实市场验收结果。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

SUPPORTED_HORIZONS = (1, 3, 5, 20)
ACCEPTANCE_STUB_PROVIDER = "deterministic-market-data-stub"
ACCEPTANCE_STUB_SYMBOL = "SHADOW"
ACCEPTANCE_STUB_BENCHMARK = "BENCH"
ACCEPTANCE_STUB_EVENT_DAY = date(2026, 7, 1)
ACCEPTANCE_STUB_BAR_COUNT = 21


class FutureDataLeakError(ValueError):
    """行情在评估 as_of 后才可用，拒绝将其用于当前评估。"""


@dataclass(frozen=True)
class MarketBar:
    symbol: str
    trading_day: date
    close: Decimal
    available_at: datetime
    suspended: bool = False

    def __post_init__(self) -> None:
        if self.available_at.tzinfo is None:
            raise ValueError("MarketBar.available_at must include a timezone")
        if self.close <= 0:
            raise ValueError("MarketBar.close must be positive")


class MarketDataProvider(Protocol):
    """可由真实行情适配器替换的最小只读契约。"""

    provider_name: str
    is_real_market_data: bool

    def get_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        as_of: datetime,
    ) -> Sequence[MarketBar]: ...


class DeterministicMarketDataProvider:
    """内存确定性 Stub；有意暴露非真实行情标记。"""

    provider_name = "deterministic-market-data-stub"
    is_real_market_data = False

    def __init__(self, bars: Mapping[str, Iterable[MarketBar]]) -> None:
        self._bars = {
            symbol: tuple(sorted(values, key=lambda item: item.trading_day))
            for symbol, values in bars.items()
        }

    def get_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        as_of: datetime,
    ) -> Sequence[MarketBar]:
        if as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        selected = [
            bar
            for bar in self._bars.get(symbol, ())
            if start <= bar.trading_day <= end
        ]
        leaked = [bar for bar in selected if bar.available_at > as_of]
        if leaked:
            first = min(leaked, key=lambda item: item.available_at)
            raise FutureDataLeakError(
                f"{symbol} {first.trading_day.isoformat()} was unavailable at as_of"
            )
        return tuple(selected)


@dataclass(frozen=True)
class HorizonReturn:
    horizon: int
    status: str
    start_day: date | None
    end_day: date | None
    security_return: float | None
    benchmark_return: float | None
    abnormal_return: float | None


@dataclass(frozen=True)
class MarketEvaluation:
    symbol: str
    benchmark_symbol: str
    as_of: datetime
    provider_name: str
    real_market_data: bool
    results: tuple[HorizonReturn, ...]

    @property
    def suitable_for_real_market_acceptance(self) -> bool:
        return self.real_market_data


def build_acceptance_market_stub(as_of: datetime) -> MarketEvaluation:
    """``mvp_acceptance`` 使用的规范行情 Stub；永远不是真实行情。

    固定起点 ``2026-07-01`` 与 21 根 bar，保证同 ``as_of`` 下可重放。
    ``real_market_data`` / ``suitable_for_real_market_acceptance`` 恒为 False。
    """

    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")

    days = [
        ACCEPTANCE_STUB_EVENT_DAY + timedelta(days=index)
        for index in range(ACCEPTANCE_STUB_BAR_COUNT)
    ]
    observation_end = min(days[-1], as_of.date())

    def bars(symbol: str, base: int) -> list[MarketBar]:
        return [
            MarketBar(
                symbol=symbol,
                trading_day=day,
                close=Decimal(base + index),
                available_at=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
            )
            for index, day in enumerate(days)
        ]

    provider = DeterministicMarketDataProvider(
        {
            ACCEPTANCE_STUB_SYMBOL: bars(ACCEPTANCE_STUB_SYMBOL, 100),
            ACCEPTANCE_STUB_BENCHMARK: bars(ACCEPTANCE_STUB_BENCHMARK, 200),
        }
    )
    return evaluate_market_returns(
        provider,
        symbol=ACCEPTANCE_STUB_SYMBOL,
        benchmark_symbol=ACCEPTANCE_STUB_BENCHMARK,
        event_day=ACCEPTANCE_STUB_EVENT_DAY,
        observation_end=observation_end,
        as_of=as_of,
    )


def acceptance_market_payload(evaluation: MarketEvaluation) -> dict[str, Any]:
    """将 Stub 评估结果序列化为验收报告 ``market`` 字段。"""

    def horizon_payload(result: HorizonReturn) -> dict[str, Any]:
        value = asdict(result)
        value["start_day"] = result.start_day.isoformat() if result.start_day else None
        value["end_day"] = result.end_day.isoformat() if result.end_day else None
        return value

    return {
        "provider": evaluation.provider_name,
        "real_market_data": evaluation.real_market_data,
        "suitable_for_real_market_acceptance": evaluation.suitable_for_real_market_acceptance,
        "horizons": [horizon_payload(result) for result in evaluation.results],
    }


def evaluate_market_returns(
    provider: MarketDataProvider,
    *,
    symbol: str,
    benchmark_symbol: str,
    event_day: date,
    as_of: datetime,
    observation_end: date,
    horizons: Sequence[int] = SUPPORTED_HORIZONS,
) -> MarketEvaluation:
    """计算有效交易日收益及基准异常收益。

    停牌 bar 和缺失日期不计入证券交易日；若窗口或对应基准 bar 不完整，返回
    ``insufficient_data``，不插值也不窥视 ``as_of`` 之后的数据。
    """

    invalid = sorted(set(horizons) - set(SUPPORTED_HORIZONS))
    if invalid:
        raise ValueError(f"unsupported horizons: {invalid}")
    if observation_end < event_day:
        raise ValueError("observation_end must not precede event_day")

    security_bars = _tradable(
        provider.get_bars(symbol, event_day, observation_end, as_of=as_of)
    )
    benchmark_bars = {
        bar.trading_day: bar
        for bar in _tradable(
            provider.get_bars(benchmark_symbol, event_day, observation_end, as_of=as_of)
        )
    }
    results = tuple(
        _horizon_return(security_bars, benchmark_bars, horizon)
        for horizon in horizons
    )
    return MarketEvaluation(
        symbol=symbol,
        benchmark_symbol=benchmark_symbol,
        as_of=as_of,
        provider_name=provider.provider_name,
        real_market_data=provider.is_real_market_data,
        results=results,
    )


def _tradable(bars: Sequence[MarketBar]) -> tuple[MarketBar, ...]:
    return tuple(
        sorted(
            (bar for bar in bars if not bar.suspended),
            key=lambda item: item.trading_day,
        )
    )


def _horizon_return(
    security_bars: Sequence[MarketBar],
    benchmark_bars: Mapping[date, MarketBar],
    horizon: int,
) -> HorizonReturn:
    if len(security_bars) <= horizon:
        return HorizonReturn(horizon, "insufficient_data", None, None, None, None, None)

    start = security_bars[0]
    end = security_bars[horizon]
    benchmark_start = benchmark_bars.get(start.trading_day)
    benchmark_end = benchmark_bars.get(end.trading_day)
    if benchmark_start is None or benchmark_end is None:
        return HorizonReturn(
            horizon,
            "insufficient_data",
            start.trading_day,
            end.trading_day,
            None,
            None,
            None,
        )

    security_return = float(end.close / start.close - Decimal("1"))
    benchmark_return = float(benchmark_end.close / benchmark_start.close - Decimal("1"))
    return HorizonReturn(
        horizon=horizon,
        status="complete",
        start_day=start.trading_day,
        end_day=end.trading_day,
        security_return=security_return,
        benchmark_return=benchmark_return,
        abnormal_return=security_return - benchmark_return,
    )
