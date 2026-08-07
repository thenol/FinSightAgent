"""影响分析自动生成触发测试。"""

from dataclasses import replace
from datetime import datetime, timezone

from app.analysis.service import ImpactAnalysisService
from app.domain import Claim, Event, FactCard, WorkflowRun
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository
from app.platform.settings import Settings
from app.publishing.service import FactCardService


def _settings(**overrides) -> Settings:
    defaults = {
        "environment": "test",
        "repository": "memory",
        "database_url": "postgresql+psycopg://x",
        "redis_url": "redis://x",
        "artifact_root": "/tmp",
        "jwt_secret": "development-only-secret-change-me-32-bytes",
        "bootstrap_admin_username": "",
        "bootstrap_admin_password": "",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_event(importance: float = 0.92, status: str = "triaged") -> Event:
    return Event(
        id=new_id("evt"),
        event_type="macro_policy",
        status=status,
        title="美联储宣布加息25个基点",
        entity_ids=[],
        document_ids=[],
        importance=importance,
        urgency="high",
        occurred_at=datetime.now(timezone.utc),
        key_fields={"policy_body": "federal_reserve", "rate_decision": "hike"},
        missing_required=[],
    )


def _make_claim(event_id: str, status: str = "verified") -> Claim:
    return Claim(
        id=new_id("clm"),
        event_id=event_id,
        subject_text="美联储",
        predicate="announces_rate_decision",
        object_value={"rate_change_bp": "25", "target_rate": "5.25%-5.50%"},
        status=status,
        confidence=0.95,
        evidence_ids=[],
        as_of=datetime.now(timezone.utc),
    )


def _make_run(event_id: str) -> WorkflowRun:
    return WorkflowRun(
        id=new_id("wfr"),
        event_id=event_id,
        trigger_id="test",
        status="succeeded",
        as_of=datetime.now(timezone.utc),
    )


class _MockImpactAnalysisService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, event_id: str, actor: str | None = None) -> None:
        self.calls.append((event_id, actor))


def test_auto_trigger_on_create_when_claim_verified() -> None:
    repo = InMemoryRepository()
    event = _make_event()
    repo.save_event(event)
    claim = _make_claim(event.id, status="verified")
    repo.save_claim(claim)
    mock = _MockImpactAnalysisService()

    FactCardService(repo, settings=_settings(), impact_service=mock).create(event, claim)

    assert len(mock.calls) == 1
    assert mock.calls[0][0] == event.id
    assert mock.calls[0][1] == "system:auto"


def test_no_auto_trigger_when_claim_unverified() -> None:
    repo = InMemoryRepository()
    event = _make_event()
    repo.save_event(event)
    claim = _make_claim(event.id, status="unverified")
    repo.save_claim(claim)
    mock = _MockImpactAnalysisService()

    FactCardService(repo, settings=_settings(), impact_service=mock).create(event, claim)

    assert mock.calls == []


def test_no_auto_trigger_for_low_importance_event() -> None:
    repo = InMemoryRepository()
    event = _make_event(importance=0.50)
    repo.save_event(event)
    claim = _make_claim(event.id, status="verified")
    repo.save_claim(claim)
    mock = _MockImpactAnalysisService()

    FactCardService(repo, settings=_settings(), impact_service=mock).create(event, claim)

    assert mock.calls == []


def test_no_auto_trigger_for_dormant_event() -> None:
    repo = InMemoryRepository()
    event = _make_event(status="dormant")
    repo.save_event(event)
    claim = _make_claim(event.id, status="verified")
    repo.save_claim(claim)
    mock = _MockImpactAnalysisService()

    FactCardService(repo, settings=_settings(), impact_service=mock).create(event, claim)

    assert mock.calls == []


def test_no_auto_trigger_when_disabled() -> None:
    repo = InMemoryRepository()
    event = _make_event()
    repo.save_event(event)
    claim = _make_claim(event.id, status="verified")
    repo.save_claim(claim)
    mock = _MockImpactAnalysisService()
    settings = _settings(auto_impact_analysis_enabled=False)

    FactCardService(repo, settings=settings, impact_service=mock).create(event, claim)

    assert mock.calls == []


def test_auto_trigger_on_transition_to_published() -> None:
    repo = InMemoryRepository()
    event = _make_event()
    repo.save_event(event)
    card = FactCard(
        id=new_id("rpt"),
        event_id=event.id,
        version=1,
        status="approved",
        title=event.title,
        summary="summary",
        claim_ids=[],
        as_of=datetime.now(timezone.utc),
    )
    repo.save_fact_card(card)
    mock = _MockImpactAnalysisService()

    FactCardService(repo, settings=_settings(), impact_service=mock).transition(
        card, "published", "publisher release"
    )

    assert len(mock.calls) == 1
    assert mock.calls[0][0] == event.id


def test_auto_trigger_on_create_from_draft_published() -> None:
    repo = InMemoryRepository()
    event = _make_event()
    repo.save_event(event)
    run = _make_run(event.id)
    mock = _MockImpactAnalysisService()

    FactCardService(repo, settings=_settings(), impact_service=mock).create_from_draft(
        event, run, {}, status="published"
    )

    assert len(mock.calls) == 1
    assert mock.calls[0][0] == event.id


def test_no_duplicate_auto_trigger_when_already_exists() -> None:
    repo = InMemoryRepository()
    event = _make_event()
    repo.save_event(event)
    # 预存一个影响分析，模拟已生成过
    analysis = ImpactAnalysisService(repo, settings=_settings()).generate(event.id)
    claim = _make_claim(event.id, status="verified")
    repo.save_claim(claim)
    mock = _MockImpactAnalysisService()

    FactCardService(repo, settings=_settings(), impact_service=mock).create(event, claim)

    assert mock.calls == []
    latest = repo.get_latest_impact_analysis_for_event(event.id)
    assert latest is not None
    assert latest.id == analysis.id


def test_auto_trigger_uses_custom_threshold() -> None:
    repo = InMemoryRepository()
    event = _make_event(importance=0.65)
    repo.save_event(event)
    claim = _make_claim(event.id, status="verified")
    repo.save_claim(claim)
    mock = _MockImpactAnalysisService()
    settings = _settings(auto_impact_analysis_importance_threshold=0.60)

    FactCardService(repo, settings=settings, impact_service=mock).create(event, claim)

    assert len(mock.calls) == 1


def test_auto_trigger_failure_does_not_block_publish() -> None:
    repo = InMemoryRepository()
    event = _make_event()
    repo.save_event(event)
    claim = _make_claim(event.id, status="verified")
    repo.save_claim(claim)

    class FailingService:
        def generate(self, event_id: str, actor: str | None = None) -> None:
            raise RuntimeError("llm unavailable")

    card = FactCardService(
        repo, settings=_settings(), impact_service=FailingService()
    ).create(event, claim)

    assert card.status == "published"
