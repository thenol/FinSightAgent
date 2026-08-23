from datetime import datetime, timedelta, timezone

from app.analysis.forward import ForwardImpactService
from app.domain import ForwardCatalyst, ForwardImpactWindow, ImpactTargetDefinition
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository


def test_forward_window_separates_expected_and_stress_events() -> None:
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    target = ImpactTargetDefinition(new_id("tgt"), "industry", "real-estate", "房地产")
    repo.save_impact_target(target)
    repo.save_forward_catalyst(
        ForwardCatalyst(
            id=new_id("fct"),
            target_id=target.id,
            kind="scheduled",
            title="议息会议",
            event_type="macro_policy",
            scheduled_from=now + timedelta(days=3),
            trigger_definition={"direction": "positive", "strength": 0.6, "confidence": 0.8},
            status="approved",
            created_at=now,
        )
    )
    repo.save_forward_catalyst(
        ForwardCatalyst(
            id=new_id("fct"),
            target_id=target.id,
            kind="hypothetical",
            title="房企违约",
            event_type="credit_event",
            trigger_definition={"direction": "negative", "strength": 0.9},
            status="approved",
            created_at=now,
        )
    )
    window = ForwardImpactWindow(
        id=new_id("fiw"),
        target_id=target.id,
        as_of=now,
        window_start=now + timedelta(days=1),
        window_end=now + timedelta(days=30),
        included_kinds=["scheduled", "hypothetical"],
        scenario_set_id="stress",
    )
    service = ForwardImpactService(repo)
    service.create_window(window)
    points = service.recompute(window.id)
    baseline = [item for item in points if item.scenario_id == "baseline"]
    stress = [item for item in points if item.scenario_id == "stress"]
    assert baseline and stress
    assert all(item.negative_conditional == 0 for item in baseline)
    assert any(item.negative_conditional > 0 for item in stress)


def test_forward_window_rejects_window_before_as_of() -> None:
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    target = ImpactTargetDefinition(new_id("tgt"), "industry", "real-estate", "房地产")
    repo.save_impact_target(target)
    window = ForwardImpactWindow(
        id=new_id("fiw"),
        target_id=target.id,
        as_of=now,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=30),
    )
    try:
        ForwardImpactService(repo).create_window(window)
    except ValueError as exc:
        assert str(exc) == "FORWARD_WINDOW_START_BEFORE_AS_OF"
    else:
        raise AssertionError("expected as_of validation failure")
