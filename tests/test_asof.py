from datetime import datetime, timedelta, timezone

import pytest

from app.domain import Claim, Event
from app.platform.asof import (
    AsOfViolation,
    default_as_of,
    ensure_within_as_of,
    visible_as_of,
)
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
PAST = NOW - timedelta(days=5)
FUTURE = NOW + timedelta(days=5)


def make_event(occurred_at: datetime) -> Event:
    return Event(
        id=new_id("evt"),
        event_type="earnings_guidance",
        status="triaged",
        title="测试事件",
        entity_ids=["000001.SZ"],
        document_ids=["doc_1"],
        importance=0.80,
        urgency="normal",
        occurred_at=occurred_at,
    )


def make_claim(event_id: str, as_of: datetime) -> Claim:
    return Claim(
        id=new_id("clm"),
        event_id=event_id,
        subject_text="000001.SZ",
        predicate="document_discloses_event",
        object_value={"type": "string", "value": "earnings_guidance"},
        status="verified",
        confidence=0.90,
        evidence_ids=["evd_1"],
        as_of=as_of,
    )


def test_visible_as_of_returns_true_when_no_cutoff() -> None:
    event = make_event(FUTURE)
    assert visible_as_of(event, None) is True


def test_visible_as_of_filters_future_event() -> None:
    future_event = make_event(FUTURE)
    past_event = make_event(PAST)
    assert visible_as_of(future_event, NOW) is False
    assert visible_as_of(past_event, NOW) is True


def test_list_events_filters_future_when_as_of_given() -> None:
    repository = InMemoryRepository()
    repository.save_event(make_event(PAST))
    repository.save_event(make_event(FUTURE))

    visible = repository.list_events(as_of=NOW)
    assert len(visible) == 1
    assert visible[0].occurred_at == PAST


def test_list_events_returns_all_when_no_as_of() -> None:
    repository = InMemoryRepository()
    repository.save_event(make_event(PAST))
    repository.save_event(make_event(FUTURE))

    assert len(repository.list_events()) == 2


def test_replay_only_reads_data_available_at_as_of() -> None:
    repository = InMemoryRepository()
    # 第一条 claim 在过去发布
    event_past = make_event(PAST)
    repository.save_event(event_past)
    claim_past = make_claim(event_past.id, as_of=PAST)
    repository.save_claim(claim_past)
    # 第二条 claim 在未来发布（模拟后续公告修订）
    future_event = make_event(FUTURE)
    repository.save_event(future_event)
    claim_future = make_claim(future_event.id, as_of=FUTURE)
    repository.save_claim(claim_future)

    # 以 NOW 回放：只能看到过去的 claim，看不到未来的
    visible_claims = repository.get_claims_for_event(future_event.id, as_of=NOW)
    assert visible_claims == []
    visible_claims_past = repository.get_claims_for_event(event_past.id, as_of=NOW)
    assert claim_past in visible_claims_past


def test_find_event_by_document_respects_as_of() -> None:
    repository = InMemoryRepository()
    future_event = make_event(FUTURE)
    future_event = Event(
        id=future_event.id,
        event_type=future_event.event_type,
        status=future_event.status,
        title=future_event.title,
        entity_ids=future_event.entity_ids,
        document_ids=["doc_future"],
        importance=future_event.importance,
        urgency=future_event.urgency,
        occurred_at=future_event.occurred_at,
    )
    repository.save_event(future_event)

    # 在 NOW 时点，未来事件尚不可见
    assert repository.find_event_by_document("doc_future", as_of=NOW) is None
    # 不传 as_of 时不过滤
    assert repository.find_event_by_document("doc_future") is not None


def test_ensure_within_as_of_raises_for_future_data() -> None:
    future_event = make_event(FUTURE)
    with pytest.raises(AsOfViolation) as exc_info:
        ensure_within_as_of(future_event, NOW, context="tool_result")
    assert "AS_OF_VIOLATION" in str(exc_info.value)


def test_ensure_within_as_of_passes_for_past_data() -> None:
    past_event = make_event(PAST)
    # 不抛异常即通过
    ensure_within_as_of(past_event, NOW)


def test_default_as_of_returns_utc_now() -> None:
    fixed = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    assert default_as_of(fixed) == fixed
