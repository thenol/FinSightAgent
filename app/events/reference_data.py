"""Versioned, time-aware reference data contract for entity resolution.

The deterministic provider is an MVP stub.  Production adapters can implement
``ReferenceDataProvider`` without coupling the resolver to an external vendor.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

IdentifierType = Literal[
    "code_exact",
    "name_full",
    "name_short",
    "historical_code",
    "historical_name",
]

_CODE = re.compile(r"(?<!\d)([036]\d{5})(?:\.(SZ|SH))?(?!\d)", re.IGNORECASE)


@dataclass(frozen=True)
class TemporalIdentifier:
    """An old code or name and the interval in which it was valid."""

    value: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None


@dataclass(frozen=True)
class ReferenceSecurity:
    """One stable entity/security identity plus its time-varying identifiers."""

    entity_id: str
    security_id: str
    market_code: str
    canonical_name: str
    full_name: str
    short_names: tuple[str, ...] = ()
    historical_codes: tuple[TemporalIdentifier, ...] = ()
    historical_names: tuple[TemporalIdentifier, ...] = ()
    valid_from: datetime | None = None
    valid_to: datetime | None = None


@dataclass(frozen=True)
class ReferenceMatch:
    entity_id: str
    security_id: str
    market_code: str
    canonical_name: str
    matched_value: str
    identifier_type: IdentifierType
    start: int
    end: int


class ReferenceDataProvider(Protocol):
    """Versioned point-in-time lookup contract."""

    @property
    def version(self) -> str: ...

    def find_matches(self, text: str, as_of: datetime) -> list[ReferenceMatch]: ...


class DeterministicReferenceDataProvider:
    """In-process deterministic stub for fixtures, replay, and contract tests."""

    def __init__(
        self,
        records: Sequence[ReferenceSecurity] = (),
        *,
        version: str = "reference-data-stub-v1",
    ) -> None:
        if not version:
            raise ValueError("reference data version must not be empty")
        self._records = tuple(records)
        self._version = version

    @property
    def version(self) -> str:
        return self._version

    def find_matches(self, text: str, as_of: datetime) -> list[ReferenceMatch]:
        instant = _aware(as_of)
        matches: list[ReferenceMatch] = []
        code_mentions = tuple(_code_mentions(text))

        for record in self._records:
            if _is_valid(instant, record.valid_from, record.valid_to):
                matches.extend(self._current_matches(text, code_mentions, record))
            matches.extend(self._historical_matches(text, code_mentions, record, instant))

        return sorted(
            matches,
            key=lambda item: (
                item.start,
                item.end,
                item.identifier_type,
                item.entity_id,
                item.security_id,
            ),
        )

    @staticmethod
    def _current_matches(
        text: str,
        code_mentions: tuple[tuple[str, int, int], ...],
        record: ReferenceSecurity,
    ) -> list[ReferenceMatch]:
        matches = _matching_codes(code_mentions, record, record.market_code, "code_exact")
        matches.extend(_matching_names(text, record, record.full_name, "name_full"))
        for short_name in record.short_names:
            matches.extend(_matching_names(text, record, short_name, "name_short"))
        return matches

    @staticmethod
    def _historical_matches(
        text: str,
        code_mentions: tuple[tuple[str, int, int], ...],
        record: ReferenceSecurity,
        as_of: datetime,
    ) -> list[ReferenceMatch]:
        matches: list[ReferenceMatch] = []
        for identifier in record.historical_codes:
            if _is_valid(as_of, identifier.valid_from, identifier.valid_to):
                matches.extend(
                    _matching_codes(
                        code_mentions,
                        record,
                        identifier.value,
                        "historical_code",
                    )
                )
        for identifier in record.historical_names:
            if _is_valid(as_of, identifier.valid_from, identifier.valid_to):
                matches.extend(
                    _matching_names(
                        text,
                        record,
                        identifier.value,
                        "historical_name",
                    )
                )
        return matches


def _matching_codes(
    mentions: tuple[tuple[str, int, int], ...],
    record: ReferenceSecurity,
    expected: str,
    identifier_type: IdentifierType,
) -> list[ReferenceMatch]:
    normalized = _normalize_market_code(expected)
    return [
        _match(record, normalized, identifier_type, start, end)
        for market_code, start, end in mentions
        if market_code == normalized
    ]


def _matching_names(
    text: str,
    record: ReferenceSecurity,
    name: str,
    identifier_type: IdentifierType,
) -> list[ReferenceMatch]:
    if not name:
        return []
    return [
        _match(record, name, identifier_type, found.start(), found.end())
        for found in re.finditer(re.escape(name), text)
    ]


def _match(
    record: ReferenceSecurity,
    matched_value: str,
    identifier_type: IdentifierType,
    start: int,
    end: int,
) -> ReferenceMatch:
    return ReferenceMatch(
        entity_id=record.entity_id,
        security_id=record.security_id,
        market_code=_normalize_market_code(record.market_code),
        canonical_name=record.canonical_name,
        matched_value=matched_value,
        identifier_type=identifier_type,
        start=start,
        end=end,
    )


def _code_mentions(text: str):
    for found in _CODE.finditer(text):
        ticker, exchange = found.groups()
        inferred = exchange.upper() if exchange else ("SH" if ticker.startswith("6") else "SZ")
        yield f"{ticker}.{inferred}", found.start(), found.end()


def _normalize_market_code(value: str) -> str:
    found = _CODE.fullmatch(value.strip())
    if not found:
        return value.strip().upper()
    ticker, exchange = found.groups()
    inferred = exchange.upper() if exchange else ("SH" if ticker.startswith("6") else "SZ")
    return f"{ticker}.{inferred}"


def _is_valid(
    as_of: datetime,
    valid_from: datetime | None,
    valid_to: datetime | None,
) -> bool:
    start = _aware(valid_from) if valid_from else None
    end = _aware(valid_to) if valid_to else None
    return (start is None or start <= as_of) and (end is None or as_of < end)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
