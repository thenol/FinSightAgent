from datetime import datetime, timedelta, timezone

from app.analysis.aggregation import ImpactAggregationService
from app.domain import Event, ImpactAnalysis
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository


def _analysis(
    repo, *, title: str, direction: str, confidence: float, importance: float
) -> ImpactAnalysis:
    now = datetime.now(timezone.utc)
    event = Event(
        id=new_id("evt"),
        event_type="macro_policy",
        status="triaged",
        title=title,
        entity_ids=[],
        document_ids=[],
        importance=importance,
        urgency="high",
        occurred_at=now,
    )
    repo.save_event(event)
    analysis = ImpactAnalysis(
        id=new_id("imp"),
        event_id=event.id,
        version=1,
        status="approved",
        event_title_snapshot=title,
        summary=title,
        transmission_chains=[],
        impacts=[
            {
                "target_type": "industry",
                "target_name": "房地产",
                "direction": direction,
                "magnitude": "strong" if direction == "negative" else "moderate",
                "horizon": "medium",
                "confidence": confidence,
            }
        ],
        macro_assumptions=[],
        watch_items=[],
        generated_by="test",
        created_at=now,
    )
    repo.save_impact_analysis(analysis)
    return analysis


def test_larger_negative_event_reverses_existing_positive_view() -> None:
    repo = InMemoryRepository()
    positive = _analysis(repo, title="降息", direction="positive", confidence=0.8, importance=0.7)
    negative = _analysis(
        repo, title="流动性危机", direction="negative", confidence=0.95, importance=1.0
    )
    service = ImpactAggregationService(repo)
    contributions = service.project_analysis(positive) + service.project_analysis(negative)
    snapshot = service.recompute_target(contributions[0].target_id)
    assert snapshot is not None
    assert snapshot.direction == "negative"
    assert snapshot.positive_gross > 0
    assert snapshot.negative_gross > snapshot.positive_gross


def test_unapproved_analysis_is_excluded() -> None:
    repo = InMemoryRepository()
    analysis = _analysis(
        repo, title="待审核事件", direction="negative", confidence=0.95, importance=1.0
    )
    repo.update_impact_analysis(
        analysis.__class__(**{**analysis.__dict__, "status": "needs_review"})
    )
    assert (
        ImpactAggregationService(repo).project_analysis(repo.get_impact_analysis(analysis.id)) == []
    )


def test_time_decay_reduces_old_contribution() -> None:
    repo = InMemoryRepository()
    analysis = _analysis(repo, title="旧事件", direction="positive", confidence=0.8, importance=0.8)
    service = ImpactAggregationService(repo)
    contribution = service.project_analysis(analysis)[0]
    old = contribution.__class__(
        **{
            **contribution.__dict__,
            "valid_from": datetime.now(timezone.utc) - timedelta(days=180),
        }
    )
    repo.impact_contributions[contribution.id] = old
    snapshot = service.recompute_target(contribution.target_id)
    assert snapshot is not None
    assert snapshot.positive_gross < contribution.base_strength
