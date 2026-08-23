from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.analysis.aggregation import ImpactAggregationService
from app.domain import Event, ImpactAnalysis, ImpactTargetMapping
from app.market.factors import EventImpactFactorService
from app.market.provider import MarketInstrument
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository

NOW = datetime(2026, 8, 18, tzinfo=timezone.utc)
INSTRUMENT = MarketInstrument(
    id="cn:index:000300",
    market="cn",
    symbol="000300",
    name="沪深300",
    instrument_type="index",
)


def _approved_impact(repo: InMemoryRepository, *, created_at: datetime = NOW) -> str:
    event = Event(
        id=new_id("evt"),
        event_type="macro_policy",
        status="triaged",
        title="政策利好",
        entity_ids=[],
        document_ids=[],
        importance=0.9,
        urgency="high",
        occurred_at=created_at - timedelta(hours=1),
    )
    repo.save_event(event)
    analysis = ImpactAnalysis(
        id=new_id("imp"),
        event_id=event.id,
        version=1,
        status="approved",
        event_title_snapshot=event.title,
        summary=event.title,
        transmission_chains=[],
        impacts=[
            {
                "target_type": "market",
                "target_name": "沪深300",
                "target_code": "instrument:cn:index:000300",
                "direction": "positive",
                "magnitude": "strong",
                "horizon": "short",
                "confidence": 0.9,
            }
        ],
        macro_assumptions=[],
        watch_items=[],
        generated_by="test",
        created_at=created_at,
    )
    repo.save_impact_analysis(analysis)
    contribution = ImpactAggregationService(repo).project_analysis(analysis)[0]
    # Projection time is the platform knowledge time; make it deterministic.
    repo.impact_contributions[contribution.id] = replace(contribution, created_at=created_at)
    repo.save_impact_target_mapping(
        ImpactTargetMapping(
            id=new_id("itm"),
            target_id=contribution.target_id,
            mapping_type="instrument",
            mapping_code=INSTRUMENT.id,
            weight=1.0,
            confidence=1.0,
            status="approved",
            created_by="test",
            created_at=NOW,
        )
    )
    return contribution.target_id


def test_event_factor_uses_explicit_mapping_and_approved_snapshot() -> None:
    repo = InMemoryRepository()
    target_id = _approved_impact(repo)

    result = EventImpactFactorService(repo).snapshot(INSTRUMENT, as_of=NOW, horizon=3)

    assert result.status == "available"
    assert result.score is not None and result.score > 0
    assert result.sources[0].target_id == target_id
    assert result.sources[0].match_kind == "approved_instrument_mapping"


def test_event_factor_does_not_treat_unmapped_target_as_neutral() -> None:
    repo = InMemoryRepository()
    _approved_impact(repo)
    other = replace(INSTRUMENT, id="cn:index:000001", symbol="000001", name="上证指数")

    result = EventImpactFactorService(repo).snapshot(other, as_of=NOW, horizon=3)

    assert result.status == "unavailable"
    assert result.score is None
    assert result.reason == "impact_target_not_mapped"


def test_event_factor_excludes_information_created_after_as_of() -> None:
    repo = InMemoryRepository()
    _approved_impact(repo, created_at=NOW + timedelta(days=1))

    result = EventImpactFactorService(repo).snapshot(INSTRUMENT, as_of=NOW, horizon=3)

    assert result.status == "unavailable"
    assert result.score is None
    assert result.reason == "approved_impact_snapshot_missing"
