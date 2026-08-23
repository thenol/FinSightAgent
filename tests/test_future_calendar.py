from datetime import date, datetime, timezone

from app.analysis.forward import ForwardImpactService
from app.domain import ForwardCatalyst, ImpactTargetDefinition
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository


def test_calendar_day_returns_scheduled_event_and_target_summary() -> None:
    repo = InMemoryRepository()
    target = ImpactTargetDefinition(new_id("tgt"), "industry", "cn-banks", "银行")
    repo.save_impact_target(target)
    repo.save_forward_catalyst(
        ForwardCatalyst(
            id=new_id("fct"),
            target_id=target.id,
            kind="scheduled",
            title="央行政策会议",
            event_type="macro_policy",
            scheduled_from=datetime(2026, 9, 15, 1, tzinfo=timezone.utc),
            scheduled_to=datetime(2026, 9, 15, 3, tzinfo=timezone.utc),
            trigger_definition={"direction": "positive", "magnitude": "moderate", "strength": 0.6},
            status="approved",
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
    )
    view = ForwardImpactService(repo).day_view(
        selected_date=date(2026, 9, 15), timezone_name="Asia/Shanghai"
    )
    assert len(view["scheduled_events"]) == 1
    assert view["active_impacts"][0]["target_name"] == "银行"
    assert view["target_summary"][0]["direction"] == "positive"


def test_calendar_excludes_candidate_by_default() -> None:
    repo = InMemoryRepository()
    target = ImpactTargetDefinition(new_id("tgt"), "industry", "cn-banks", "银行")
    repo.save_impact_target(target)
    repo.save_forward_catalyst(
        ForwardCatalyst(
            id=new_id("fct"),
            target_id=target.id,
            kind="conditional",
            title="待审核事件",
            event_type="macro_policy",
            scheduled_from=datetime(2026, 9, 15, tzinfo=timezone.utc),
            status="candidate",
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
    )
    events = ForwardImpactService(repo).list_calendar_events(
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )
    assert events == []


def test_calendar_summary_contains_ranked_event_previews() -> None:
    repo = InMemoryRepository()
    target = ImpactTargetDefinition(new_id("tgt"), "industry", "cn-banks", "银行")
    repo.save_impact_target(target)
    repo.save_forward_catalyst(
        ForwardCatalyst(
            id=new_id("fct"),
            target_id=target.id,
            kind="scheduled",
            title="重要政策会议",
            event_type="macro_policy",
            scheduled_from=datetime(2026, 9, 15, 1, tzinfo=timezone.utc),
            trigger_definition={"direction": "positive", "importance": 0.9},
            status="approved",
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
    )
    summary = ForwardImpactService(repo).calendar_summary(
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc),
    )
    item = next(day for day in summary if day["date"] == "2026-09-15")
    assert item["event_count"] == 1
    assert item["event_previews"][0]["title"] == "重要政策会议"
    assert item["hidden_event_count"] == 0
