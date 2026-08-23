from datetime import date, datetime, timezone

from app.market.calendar import ExchangeTradingCalendar, ReferenceTradingCalendar


def test_reference_calendar_uses_market_sessions_and_timezone() -> None:
    result = ReferenceTradingCalendar().query(
        market="cn",
        start=date(2026, 8, 17),
        end=date(2026, 8, 18),
        as_of=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    assert result.status == "degraded"
    assert result.warnings == ["holiday_source_not_configured"]
    assert result.calendar[0].is_open is True
    assert result.calendar[0].timezone == "Asia/Shanghai"
    assert result.calendar[0].sessions[0][0].hour == 9
    assert result.calendar[0].sessions[0][0].tzinfo is not None


def test_reference_calendar_marks_weekend_closed() -> None:
    result = ReferenceTradingCalendar().query(
        market="us",
        start=date(2026, 8, 22),
        end=date(2026, 8, 23),
        as_of=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    assert all(item.is_open is False for item in result.calendar)


def test_exchange_calendar_respects_us_holiday_and_half_day() -> None:
    calendar = ExchangeTradingCalendar()
    result = calendar.query(
        market="us", start=date(2026, 11, 26), end=date(2026, 11, 27),
        as_of=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )

    assert result.status == "ok"
    assert result.calendar[0].is_open is False  # Thanksgiving
    assert result.calendar[1].is_open is True
    assert result.calendar[1].sessions[0][1].hour == 13  # NYSE early close
    assert result.calendar[1].source == "exchange_calendars:XNYS"


def test_exchange_calendar_counts_real_sessions() -> None:
    calendar = ExchangeTradingCalendar()

    assert calendar.count_open_days("us", date(2026, 11, 23), date(2026, 11, 27)) == 4
