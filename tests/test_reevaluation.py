"""DD-22：cold 状态、WatchTrigger 注册与重估服务。"""

from datetime import datetime, timezone

from app.application.pipeline import EventResearchPipeline
from app.domain import Document
from app.events.reevaluation import ReevaluationService
from app.events.schemas import OUT_OF_SCOPE
from app.events.service import EventService
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository


def _document(
    title: str,
    content: str,
    *,
    source_id: str = "src_1",
    tier: str = "C",
) -> Document:
    now = datetime.now(timezone.utc)
    return Document(
        id=new_id("doc"),
        source_id=source_id,
        source_tier=tier,
        external_id=new_id("ext"),
        canonical_url=f"https://example.test/{new_id('u')}",
        title=title,
        content=content,
        content_hash=new_id("h"),
        published_at=now,
        ingested_at=now,
    )


def _non_finance_document(source_id: str = "src_1", tier: str = "C") -> Document:
    return _document(
        "某球队赢得联赛冠军",
        "昨晚决赛落幕，球迷走上街头庆祝胜利。",
        source_id=source_id,
        tier=tier,
    )


def test_irrelevant_event_lands_cold_not_archived() -> None:
    repository = InMemoryRepository()
    service = EventService(repository)
    document = _non_finance_document()
    repository.save_document(document)

    event = service.create_event(document)

    assert event.event_type == OUT_OF_SCOPE
    assert event.status == "cold"
    # 信息完整保留：文档可检索
    assert repository.get_document(document.id) is not None


def test_cold_event_arms_default_watch_triggers() -> None:
    repository = InMemoryRepository()
    service = EventService(repository)
    document = _non_finance_document()
    repository.save_document(document)
    event = service.create_event(document)

    triggers = repository.list_watch_triggers(event_id=event.id)
    assert {t.trigger_type for t in triggers} == {"source_cluster", "source_upgrade"}
    assert all(t.status == "armed" for t in triggers)
    upgrade = next(t for t in triggers if t.trigger_type == "source_upgrade")
    assert upgrade.condition["baseline_tier"] == "C"


def test_source_cluster_trigger_fires_and_upgrades_event() -> None:
    repository = InMemoryRepository()
    service = EventService(repository)
    doc1 = _non_finance_document("src_1")
    repository.save_document(doc1)
    event = service.create_event(doc1)
    assert event.status == "cold"

    # 同一事件陆续被另外两个独立来源报道
    for source_id in ("src_2", "src_3"):
        doc = _non_finance_document(source_id)
        repository.save_document(doc)
        event = service.attach_document_to_event(event, doc)

    result = ReevaluationService(repository).run_once()

    assert result.scanned >= 2
    assert result.fired >= 1
    assert event.id in result.upgraded_event_ids
    updated = repository.get_event(event.id)
    assert updated is not None
    assert updated.status == "needs_review"
    assert "reevaluation_confirm" in (updated.missing_required or [])
    cluster = repository.list_watch_triggers(event_id=event.id, status="fired")
    assert any(t.trigger_type == "source_cluster" for t in cluster)
    audits = [
        item for item in repository.list_audit_logs() if item.action == "event.reevaluated"
    ]
    assert audits
    assert audits[0].details["trigger_type"] == "source_cluster"
    assert audits[0].details["evidence"]["actual_sources"] == 3


def test_source_upgrade_trigger_fires_on_higher_tier() -> None:
    repository = InMemoryRepository()
    service = EventService(repository)
    doc1 = _non_finance_document("src_low", tier="C")
    repository.save_document(doc1)
    event = service.create_event(doc1)

    doc2 = _non_finance_document("src_official", tier="S")
    repository.save_document(doc2)
    service.attach_document_to_event(event, doc2)

    result = ReevaluationService(repository).run_once()

    assert result.fired >= 1
    fired = repository.list_watch_triggers(event_id=event.id, status="fired")
    assert any(t.trigger_type == "source_upgrade" for t in fired)


def test_unfired_trigger_stays_armed() -> None:
    repository = InMemoryRepository()
    service = EventService(repository)
    document = _non_finance_document()
    repository.save_document(document)
    event = service.create_event(document)

    result = ReevaluationService(repository).run_once()

    assert result.fired == 0
    assert repository.get_event(event.id).status == "cold"
    assert all(
        t.status == "armed" for t in repository.list_watch_triggers(event_id=event.id)
    )


def test_trigger_cancelled_when_event_no_longer_reevaluable() -> None:
    repository = InMemoryRepository()
    service = EventService(repository)
    document = _non_finance_document()
    repository.save_document(document)
    event = service.create_event(document)

    # 人工归档后触发器应被取消而不是悬挂
    from dataclasses import replace

    repository.update_event(replace(event, status="archived"))
    result = ReevaluationService(repository).run_once()

    assert result.fired == 0
    assert all(
        t.status == "cancelled" for t in repository.list_watch_triggers(event_id=event.id)
    )


def test_cold_event_does_not_auto_trigger_workflow() -> None:
    from app.platform.settings import Settings

    repository = InMemoryRepository()
    settings = Settings.from_environment()
    pipeline = EventResearchPipeline(repository, settings=settings)
    now = datetime.now(timezone.utc)
    result = pipeline.process(
        idempotency_key="cold-wf-1",
        source_id="src_x",
        source_tier="C",
        external_id="x1",
        url="https://example.test/x1",
        title="某球队赢得联赛冠军",
        content="昨晚决赛落幕，球迷走上街头庆祝胜利。",
        published_at=now,
    )
    assert result.event.status == "cold"
    assert repository.list_workflow_runs(event_id=result.event.id) == []
