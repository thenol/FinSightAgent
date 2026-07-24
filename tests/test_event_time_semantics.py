from datetime import datetime, timezone

import pytest

from app.domain import Document
from app.events.service import EventService
from app.events.time_parser import DeterministicEventTimeParser
from app.platform.repository import InMemoryRepository

PUBLISHED_AT = datetime(2026, 7, 12, 9, 30, tzinfo=timezone.utc)
INGESTED_AT = datetime(2026, 7, 12, 9, 31, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("event_type", "key_fields", "text", "expected", "method"),
    [
        (
            "earnings_guidance",
            {"period": "2026-半年度"},
            "公司预计2026年半年度净利润增长。",
            datetime(2026, 6, 30, tzinfo=timezone.utc),
            "period_end",
        ),
        (
            "major_contract",
            {},
            "公司于2026年7月10日与客户签订重大合同。",
            datetime(2026, 7, 10, tzinfo=timezone.utc),
            "explicit_text_date",
        ),
        (
            "merger_acquisition",
            {},
            "公司于2026年7月8日完成对目标公司的收购。",
            datetime(2026, 7, 8, tzinfo=timezone.utc),
            "explicit_text_date",
        ),
        (
            "shareholder_reduction",
            {},
            "股东于2026年7月6日完成减持。",
            datetime(2026, 7, 6, tzinfo=timezone.utc),
            "explicit_text_date",
        ),
        (
            "regulatory_penalty",
            {},
            "公司于2026年7月5日收到证监会行政处罚决定书。",
            datetime(2026, 7, 5, tzinfo=timezone.utc),
            "explicit_text_date",
        ),
    ],
)
def test_resolves_representative_business_time_for_each_event_type(
    event_type: str,
    key_fields: dict,
    text: str,
    expected: datetime,
    method: str,
) -> None:
    resolution = DeterministicEventTimeParser().parse(
        event_type=event_type,
        key_fields=key_fields,
        text=text,
        published_at=PUBLISHED_AT,
        ingested_at=INGESTED_AT,
    )

    assert resolution.occurred_at == expected
    assert resolution.occurred_at.tzinfo is not None
    assert resolution.resolution_method == method
    assert resolution.explanation
    assert resolution.parser_version


def test_recognized_key_field_date_takes_precedence() -> None:
    resolution = DeterministicEventTimeParser().parse(
        event_type="major_contract",
        key_fields={"signing_date": "2026-07-09"},
        text="公司于2026年7月10日签订重大合同。",
        published_at=PUBLISHED_AT,
        ingested_at=INGESTED_AT,
    )

    assert resolution.occurred_at == datetime(2026, 7, 9, tzinfo=timezone.utc)
    assert resolution.resolution_method == "key_field"


def test_ambiguous_business_dates_fall_back_without_guessing() -> None:
    resolution = DeterministicEventTimeParser().parse(
        event_type="regulatory_penalty",
        key_fields={},
        text="监管机构于2026年7月4日作出处罚，公司于2026年7月5日收到处罚决定书。",
        published_at=PUBLISHED_AT,
        ingested_at=INGESTED_AT,
    )

    assert resolution.occurred_at == PUBLISHED_AT
    assert resolution.resolution_method == "published_at_fallback"
    assert "ambiguous" in resolution.explanation


def test_future_business_date_falls_back_to_published_at() -> None:
    resolution = DeterministicEventTimeParser().parse(
        event_type="shareholder_reduction",
        key_fields={"reduction_date": "2026-08-01"},
        text="股东拟于2026年8月1日减持股份。",
        published_at=PUBLISHED_AT,
        ingested_at=INGESTED_AT,
        as_of=PUBLISHED_AT,
    )

    assert resolution.occurred_at == PUBLISHED_AT
    assert resolution.occurred_at <= PUBLISHED_AT
    assert resolution.occurred_at <= INGESTED_AT
    assert resolution.resolution_method == "published_at_fallback"


def test_no_business_date_falls_back_to_published_at() -> None:
    resolution = DeterministicEventTimeParser().parse(
        event_type="major_contract",
        key_fields={},
        text="公司近日与客户签订重大合同。",
        published_at=PUBLISHED_AT,
        ingested_at=INGESTED_AT,
    )

    assert resolution.occurred_at == PUBLISHED_AT
    assert resolution.resolution_method == "published_at_fallback"


def test_event_service_uses_resolved_time_and_exposes_resolution_metadata() -> None:
    document = Document(
        id="doc-time-1",
        source_id="official",
        source_tier="S",
        external_id="contract-1",
        canonical_url="https://example.test/contract-1",
        title="示例公司重大合同公告",
        content="公司于2026年7月10日与客户签订重大合同，合同金额为1亿元。",
        content_hash="hash",
        published_at=PUBLISHED_AT,
        ingested_at=INGESTED_AT,
    )
    service = EventService(InMemoryRepository())

    event = service.create_event(document)
    resolution = service.get_time_resolution(event.id)

    assert event.occurred_at == datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert resolution is not None
    assert resolution.resolution_method == "explicit_text_date"
