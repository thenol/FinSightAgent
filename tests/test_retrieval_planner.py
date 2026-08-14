from datetime import datetime, timezone

from app.domain import Entity, Event
from app.platform.repository import InMemoryRepository
from app.retrieval.planner import QueryPlanner


def _seed_event_with_entity(repository: InMemoryRepository) -> tuple[Entity, Event]:
    entity = Entity(
        id="ent_001",
        entity_type="organization",
        canonical_name="美联储",
        status="active",
    )
    event = Event(
        id="evt_001",
        event_type="macro_policy",
        status="triaged",
        title="美联储宣布加息25个基点",
        entity_ids=[entity.id],
        document_ids=[],
        importance=0.85,
        urgency="normal",
        occurred_at=datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc),
    )
    repository.save_entity(entity)
    repository.save_event(event)
    return entity, event


def test_planner_resolves_entity_and_event_type() -> None:
    repository = InMemoryRepository()
    _seed_event_with_entity(repository)
    planner = QueryPlanner(repository)
    plan = planner.plan("美联储加息对银行的影响")

    assert "ent_001" in plan.intents[0].entity_ids
    assert "macro_policy" in plan.intents[0].event_types
    assert plan.intents[0].intent == "impact_analysis"
    assert "graph" in plan.backends
    assert plan.primary_backend == "graph"


def test_planner_extracts_time_range() -> None:
    repository = InMemoryRepository()
    _seed_event_with_entity(repository)
    planner = QueryPlanner(repository)
    plan = planner.plan("2026-08-01 到 2026-08-10 的美联储事件")

    start, end = plan.intents[0].time_range
    assert start is not None and start.day == 1
    assert end is not None and end.day == 10


def test_planner_recognizes_timeline_intent() -> None:
    repository = InMemoryRepository()
    _seed_event_with_entity(repository)
    planner = QueryPlanner(repository)
    plan = planner.plan("最近7天发生了什么")

    assert plan.intents[0].intent == "timeline"
    assert "timeseries" in plan.backends
    start, end = plan.intents[0].time_range
    assert start is not None
    assert end is not None


def test_planner_document_search_defaults_to_hybrid() -> None:
    repository = InMemoryRepository()
    _seed_event_with_entity(repository)
    planner = QueryPlanner(repository)
    plan = planner.plan("查找关于银行净息差的原文文档")

    assert plan.intents[0].intent == "document_search"
    assert plan.backends == ["hybrid"]
    assert plan.primary_backend == "hybrid"
