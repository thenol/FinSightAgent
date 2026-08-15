from datetime import date, datetime, timezone

from app.domain import Event, FactCard
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository
from app.publishing.briefs import BRIEF_RULE_VERSION, DEFAULT_COMPANY_LIMIT, BriefService


def _seed_report(
    repository: InMemoryRepository,
    *,
    event_id: str,
    entity_ids: list[str],
    importance: float,
    urgency: str,
    published_at: datetime,
    version: int = 1,
    report_type: str = "research_card",
    event_type: str = "earnings_guidance",
) -> None:
    repository.save_event(
        Event(
            id=event_id,
            event_type=event_type,
            status="triaged",
            title=f"事件 {event_id}",
            entity_ids=entity_ids,
            document_ids=[],
            importance=importance,
            urgency=urgency,
            occurred_at=published_at,
        )
    )
    repository.save_fact_card(
        FactCard(
            id=new_id("rpt"),
            event_id=event_id,
            version=version,
            status="published",
            title=f"报告 {event_id}",
            summary="测试",
            claim_ids=[],
            as_of=published_at,
            report_type=report_type,
        )
    )


def test_brief_ranks_by_score_descending() -> None:
    repository = InMemoryRepository()
    day = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
    _seed_report(
        repository,
        event_id="evt_a",
        entity_ids=["000001.SZ"],
        importance=0.9,
        urgency="high",
        published_at=day,
    )
    _seed_report(
        repository,
        event_id="evt_b",
        entity_ids=["000002.SZ"],
        importance=0.3,
        urgency="low",
        published_at=day,
    )

    brief = BriefService(repository).generate(date(2026, 7, 12))

    assert brief.candidate_count == 2
    assert brief.entries[0].event_id == "evt_a"
    assert brief.entries[0].score > brief.entries[1].score
    assert brief.entries[0].rank == 1
    assert brief.rule_version == BRIEF_RULE_VERSION


def test_brief_keeps_only_latest_version_per_event() -> None:
    repository = InMemoryRepository()
    day = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
    _seed_report(
        repository,
        event_id="evt_x",
        entity_ids=["000001.SZ"],
        importance=0.5,
        urgency="normal",
        published_at=day,
        version=1,
    )
    _seed_report(
        repository,
        event_id="evt_x",
        entity_ids=["000001.SZ"],
        importance=0.8,
        urgency="high",
        published_at=day,
        version=2,
    )

    brief = BriefService(repository).generate(date(2026, 7, 12))

    # 同一 Event 只保留最新版本
    assert brief.candidate_count == 1
    assert brief.entries[0].importance == 0.8


def test_brief_caps_company_to_two_entries() -> None:
    repository = InMemoryRepository()
    day = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
    # 同一公司三条报告，urgency 均非 critical
    for i, imp in enumerate((0.9, 0.8, 0.7), start=1):
        _seed_report(
            repository,
            event_id=f"evt_c{i}",
            entity_ids=["000001.SZ"],
            importance=imp,
            urgency="normal",
            published_at=day,
        )

    brief = BriefService(repository).generate(date(2026, 7, 12))

    assert len(brief.entries) == DEFAULT_COMPANY_LIMIT


def test_brief_allows_critical_beyond_company_cap() -> None:
    repository = InMemoryRepository()
    day = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
    # 同一公司三条，第三条 critical 可突破上限
    _seed_report(
        repository,
        event_id="evt_c1",
        entity_ids=["000001.SZ"],
        importance=0.9,
        urgency="normal",
        published_at=day,
    )
    _seed_report(
        repository,
        event_id="evt_c2",
        entity_ids=["000001.SZ"],
        importance=0.8,
        urgency="normal",
        published_at=day,
    )
    _seed_report(
        repository,
        event_id="evt_c3",
        entity_ids=["000001.SZ"],
        importance=0.7,
        urgency="critical",
        published_at=day,
    )

    brief = BriefService(repository).generate(date(2026, 7, 12))

    assert len(brief.entries) == 3
    # critical 事件突破了公司 2 条上限（按 score 降序，critical 通常排前）
    assert any(e.urgency == "critical" for e in brief.entries)


def test_brief_is_stable_replay_same_output() -> None:
    """同输入同输出，不重新调用研究 Agent。"""
    repository = InMemoryRepository()
    day = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
    _seed_report(
        repository,
        event_id="evt_a",
        entity_ids=["000001.SZ"],
        importance=0.9,
        urgency="high",
        published_at=day,
    )

    service = BriefService(repository)
    first = service.generate(date(2026, 7, 12))
    second = service.generate(date(2026, 7, 12))

    # 第二次直接重放已持久化简报
    assert second.id == first.id
    assert second.candidate_count == first.candidate_count
    assert [e.event_id for e in second.entries] == [e.event_id for e in first.entries]


def test_brief_excludes_non_published_reports() -> None:
    repository = InMemoryRepository()
    day = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
    repository.save_event(
        Event(
            id="evt_p",
            event_type="earnings_guidance",
            status="triaged",
            title="x",
            entity_ids=["000001.SZ"],
            document_ids=[],
            importance=0.9,
            urgency="high",
            occurred_at=day,
        )
    )
    repository.save_fact_card(
        FactCard(
            id=new_id("rpt"),
            event_id="evt_p",
            version=1,
            status="review_required",
            title="x",
            summary="x",
            claim_ids=[],
            as_of=day,
            report_type="research_card",
        )
    )

    brief = BriefService(repository).generate(date(2026, 7, 12))

    assert brief.candidate_count == 0
    assert brief.entries == []


def test_brief_excludes_reports_outside_date_range() -> None:
    repository = InMemoryRepository()
    day = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
    other_day = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
    _seed_report(
        repository,
        event_id="evt_in",
        entity_ids=["000001.SZ"],
        importance=0.9,
        urgency="high",
        published_at=day,
    )
    _seed_report(
        repository,
        event_id="evt_out",
        entity_ids=["000002.SZ"],
        importance=0.9,
        urgency="high",
        published_at=other_day,
    )

    brief = BriefService(repository).generate(date(2026, 7, 12))

    assert brief.candidate_count == 1
    assert brief.entries[0].event_id == "evt_in"


def test_brief_excludes_candidate_type_events() -> None:
    """候选类型事件（Router 开放分类、待人工确认）不进每日简报（DD-21 §2.4）。"""
    repository = InMemoryRepository()
    day = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
    _seed_report(
        repository,
        event_id="evt_normal",
        entity_ids=["000001.SZ"],
        importance=0.6,
        urgency="normal",
        published_at=day,
    )
    _seed_report(
        repository,
        event_id="evt_candidate",
        entity_ids=["000002.SZ"],
        importance=0.95,
        urgency="high",
        published_at=day,
        event_type="geopolitical_crisis",  # 一等词表外的候选标签
    )

    brief = BriefService(repository).generate(date(2026, 7, 12))

    assert brief.candidate_count == 1
    assert brief.entries[0].event_id == "evt_normal"
