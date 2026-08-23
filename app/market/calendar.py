"""Trading-session reference service with explicit holiday-source degradation."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.market.provider import MarketDataCapability, MarketDataResult, TradingCalendarDay

_SESSIONS = {
    "cn": ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))),
    "hk": ((time(9, 30), time(12, 0)), (time(13, 0), time(16, 0))),
    "us": ((time(9, 30), time(16, 0)),),
}
_TIMEZONES = {"cn": "Asia/Shanghai", "hk": "Asia/Hong_Kong", "us": "America/New_York"}
_EXCHANGE_CODES = {"cn": "XSHG", "hk": "XHKG", "us": "XNYS"}


class ReferenceTradingCalendar:
    """Weekday reference calendar until an exchange calendar adapter is configured."""

    def __init__(self, holidays: dict[str, set[date]] | None = None) -> None:
        self.holidays = holidays or {}

    def query(self, *, market: str, start: date, end: date, as_of: datetime) -> MarketDataResult:
        if market not in _SESSIONS:
            raise ValueError(f"unsupported market: {market}")
        if end < start:
            raise ValueError("calendar range is invalid")
        timezone_name = _TIMEZONES[market]
        tz = ZoneInfo(timezone_name)
        holiday_days = self.holidays.get(market, set())
        values: list[TradingCalendarDay] = []
        cursor = start
        while cursor <= end:
            is_open = cursor.weekday() < 5 and cursor not in holiday_days
            sessions = tuple(
                (
                    datetime.combine(cursor, begin, tzinfo=tz),
                    datetime.combine(cursor, finish, tzinfo=tz),
                )
                for begin, finish in _SESSIONS[market]
            ) if is_open else ()
            values.append(
                TradingCalendarDay(
                    market=market,
                    trading_day=cursor,
                    is_open=is_open,
                    sessions=sessions,
                    timezone=timezone_name,
                    source="weekday_reference",
                    available_at=as_of,
                )
            )
            cursor += timedelta(days=1)
        return MarketDataResult(
            status="degraded",
            calendar=values,
            capability=MarketDataCapability(
                provider="weekday-reference",
                status="degraded",
                supported_markets=[market],
                reason="exchange_holiday_source_not_configured",
            ),
            warnings=["holiday_source_not_configured"],
        )

    def count_open_days(self, market: str, start: date, end: date) -> int:
        if market not in _SESSIONS or end < start:
            return 0
        holidays = self.holidays.get(market, set())
        cursor = start
        count = 0
        while cursor <= end:
            if cursor.weekday() < 5 and cursor not in holidays:
                count += 1
            cursor += timedelta(days=1)
        return count


class ExchangeTradingCalendar:
    """A/H/US exchange sessions backed by ``exchange_calendars``."""

    def __init__(self) -> None:
        import exchange_calendars as xcals
        import pandas as pd

        self._xcals = xcals
        self._pd = pd
        self._calendars = {
            market: xcals.get_calendar(exchange)
            for market, exchange in _EXCHANGE_CODES.items()
        }

    def query(self, *, market: str, start: date, end: date, as_of: datetime) -> MarketDataResult:
        if market not in self._calendars:
            raise ValueError(f"unsupported market: {market}")
        if end < start:
            raise ValueError("calendar range is invalid")
        schedule = self._calendars[market].schedule.loc[start.isoformat():end.isoformat()]
        rows = {index.date(): row for index, row in schedule.iterrows()}
        tz = ZoneInfo(_TIMEZONES[market])
        values: list[TradingCalendarDay] = []
        cursor = start
        while cursor <= end:
            row = rows.get(cursor)
            sessions: tuple[tuple[datetime, datetime], ...] = ()
            if row is not None:
                opened = row["open"].to_pydatetime().astimezone(tz)
                closed = row["close"].to_pydatetime().astimezone(tz)
                if not self._pd.isna(row["break_start"]):
                    break_start = row["break_start"].to_pydatetime().astimezone(tz)
                    break_end = row["break_end"].to_pydatetime().astimezone(tz)
                    sessions = ((opened, break_start), (break_end, closed))
                else:
                    sessions = ((opened, closed),)
            values.append(
                TradingCalendarDay(
                    market=market, trading_day=cursor, is_open=row is not None,
                    sessions=sessions, timezone=_TIMEZONES[market],
                    source=f"exchange_calendars:{_EXCHANGE_CODES[market]}", available_at=as_of,
                )
            )
            cursor += timedelta(days=1)
        return MarketDataResult(
            status="ok", calendar=values,
            capability=MarketDataCapability(
                provider="exchange-calendars", status="available",
                supported_markets=[market], reason="official_exchange_schedule_rules",
            ),
        )

    def count_open_days(self, market: str, start: date, end: date) -> int:
        if market not in self._calendars or end < start:
            return 0
        return len(self._calendars[market].sessions_in_range(start.isoformat(), end.isoformat()))


def build_trading_calendar():
    try:
        return ExchangeTradingCalendar()
    except ImportError:
        return ReferenceTradingCalendar()
