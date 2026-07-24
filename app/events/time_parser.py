"""Deterministic business-time resolution for supported event types."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Optional

PARSER_VERSION = "deterministic-event-time-v1"

_DATE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})\s*(?:年|[-/.])\s*"
    r"(?P<month>0?[1-9]|1[0-2])\s*(?:月|[-/.])\s*"
    r"(?P<day>0?[1-9]|[12]\d|3[01])\s*日?(?!\d)"
)
_CLAUSE = re.compile(r"[^。；;!\n！？]+")

_DATE_FIELDS = {
    "earnings_guidance": (
        "period_end",
        "reporting_period_end",
        "report_date",
        "event_date",
        "occurred_at",
    ),
    "major_contract": (
        "contract_date",
        "signing_date",
        "signed_at",
        "award_date",
        "event_date",
        "occurred_at",
    ),
    "merger_acquisition": (
        "transaction_date",
        "completion_date",
        "closing_date",
        "signed_at",
        "event_date",
        "occurred_at",
    ),
    "shareholder_reduction": (
        "reduction_date",
        "completion_date",
        "completed_at",
        "event_date",
        "occurred_at",
    ),
    "regulatory_penalty": (
        "penalty_date",
        "decision_date",
        "received_at",
        "event_date",
        "occurred_at",
    ),
}

_TEXT_TRIGGERS = {
    "earnings_guidance": re.compile(r"业绩|报告期|期间|截至"),
    "major_contract": re.compile(r"签订|签署|中标|中标通知书|重大合同"),
    "merger_acquisition": re.compile(r"收购|并购|合并|重组|交割|完成"),
    "shareholder_reduction": re.compile(r"减持"),
    "regulatory_penalty": re.compile(r"处罚|监管措施|警示函|立案调查|处罚决定"),
}

_PERIOD_ENDS = {
    "一季度": (3, 31),
    "Q1": (3, 31),
    "二季度": (6, 30),
    "Q2": (6, 30),
    "半年度": (6, 30),
    "中期": (6, 30),
    "H1": (6, 30),
    "三季度": (9, 30),
    "Q3": (9, 30),
    "前三季度": (9, 30),
    "四季度": (12, 31),
    "Q4": (12, 31),
    "年度": (12, 31),
    "全年": (12, 31),
}
_PERIOD = re.compile(
    r"(?P<year>20\d{2})\s*(?:年|-)?\s*"
    r"(?P<period>前三季度|[一二三四]季度|半年度|中期|年度|全年|H1|Q[1-4])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EventTimeResolution:
    occurred_at: datetime
    resolution_method: str
    explanation: str
    parser_version: str = PARSER_VERSION


class DeterministicEventTimeParser:
    """Resolve event time without guessing between competing or future dates."""

    version = PARSER_VERSION

    def parse(
        self,
        *,
        event_type: str,
        key_fields: dict[str, Any],
        text: str,
        published_at: datetime,
        ingested_at: datetime,
        as_of: Optional[datetime] = None,
    ) -> EventTimeResolution:
        published = self._aware(published_at)
        ingested = self._aware(ingested_at)
        boundary = min(published, ingested, self._aware(as_of) if as_of else published)
        tz = published.tzinfo or timezone.utc

        field_candidates = list(self._field_candidates(event_type, key_fields, tz))
        resolution = self._resolve_candidates(
            field_candidates,
            boundary,
            "key_field",
            "recognized event date in key_fields",
        )
        if resolution:
            return resolution
        if len({value for value in field_candidates if value <= boundary}) > 1:
            return self._fallback(
                published,
                boundary,
                "multiple distinct trusted dates in key_fields",
            )

        if event_type == "earnings_guidance" and "period" in key_fields:
            period_end = self._period_end(key_fields["period"], tz)
            if period_end is not None:
                if period_end <= boundary:
                    return EventTimeResolution(
                        occurred_at=period_end,
                        resolution_method="period_end",
                        explanation="resolved reporting period to its calendar end date",
                    )
                return self._fallback(
                    published,
                    boundary,
                    "reporting period end is later than the availability boundary",
                )

        text_candidates = list(self._text_candidates(event_type, text, tz))
        resolution = self._resolve_candidates(
            text_candidates,
            boundary,
            "explicit_text_date",
            "single explicit business date in an event-related clause",
        )
        if resolution:
            return resolution

        valid_text_dates = {value for value in text_candidates if value <= boundary}
        if len(valid_text_dates) > 1:
            reason = "multiple distinct business dates are ambiguous"
        elif text_candidates and not valid_text_dates:
            reason = "explicit business date is later than the availability boundary"
        else:
            reason = "no trustworthy explicit business date or period end"
        return self._fallback(published, boundary, reason)

    def _field_candidates(
        self,
        event_type: str,
        key_fields: dict[str, Any],
        tz: timezone,
    ) -> Iterable[datetime]:
        for name in _DATE_FIELDS.get(event_type, ()):
            if name not in key_fields:
                continue
            value = self._coerce_date(key_fields[name], tz)
            if value is not None:
                yield value

    def _text_candidates(
        self,
        event_type: str,
        text: str,
        tz: timezone,
    ) -> Iterable[datetime]:
        trigger = _TEXT_TRIGGERS.get(event_type)
        if trigger is None:
            return
        for clause_match in _CLAUSE.finditer(text):
            clause = clause_match.group(0)
            triggers = list(trigger.finditer(clause))
            if not triggers:
                continue
            for match in _DATE.finditer(clause):
                if min(
                    abs(match.start() - item.end()) if match.start() >= item.end()
                    else abs(item.start() - match.end())
                    for item in triggers
                ) > 40:
                    continue
                value = self._match_date(match, tz)
                if value is not None:
                    yield value

    def _resolve_candidates(
        self,
        candidates: list[datetime],
        boundary: datetime,
        method: str,
        explanation: str,
    ) -> Optional[EventTimeResolution]:
        valid = {value for value in candidates if value <= boundary}
        if len(valid) != 1:
            return None
        return EventTimeResolution(
            occurred_at=next(iter(valid)),
            resolution_method=method,
            explanation=explanation,
        )

    def _period_end(self, value: Any, tz: timezone) -> Optional[datetime]:
        if not isinstance(value, str):
            return None
        match = _PERIOD.search(value)
        if not match:
            return None
        period = match.group("period").upper()
        month_day = _PERIOD_ENDS.get(period) or _PERIOD_ENDS.get(match.group("period"))
        if month_day is None:
            return None
        return datetime(int(match.group("year")), *month_day, tzinfo=tz)

    def _coerce_date(self, value: Any, tz: timezone) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value.replace(tzinfo=tz) if value.tzinfo is None else value
        if isinstance(value, date):
            return datetime.combine(value, time.min, tzinfo=tz)
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            match = _DATE.search(value)
            return self._match_date(match, tz) if match else None
        return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed

    def _match_date(self, match: re.Match[str], tz: timezone) -> Optional[datetime]:
        try:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                tzinfo=tz,
            )
        except ValueError:
            return None

    def _fallback(
        self,
        published_at: datetime,
        boundary: datetime,
        reason: str,
    ) -> EventTimeResolution:
        occurred_at = min(published_at, boundary)
        suffix = "" if occurred_at == published_at else "; capped at availability boundary"
        return EventTimeResolution(
            occurred_at=occurred_at,
            resolution_method="published_at_fallback",
            explanation=f"{reason}; used document published_at{suffix}",
        )

    def _aware(self, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
