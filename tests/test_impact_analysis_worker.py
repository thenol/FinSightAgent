"""影响分析异步 worker 测试。"""

from datetime import datetime, timedelta, timezone

from app.analysis.worker import MAX_ATTEMPTS, ImpactAnalysisWorker
from app.domain import Event, ImpactAnalysis
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository
from app.platform.settings import Settings


def _settings() -> Settings:
    return Settings(
        environment="test",
        repository="memory",
        database_url="postgresql+psycopg://x",
        redis_url="redis://x",
        artifact_root="/tmp",
        jwt_secret="development-only-secret-change-me-32-bytes",
        bootstrap_admin_username="",
        bootstrap_admin_password="",
    )


def _save_event(repo: InMemoryRepository) -> Event:
    event = Event(
        id=new_id("evt"),
        event_type="macro_policy",
        status="triaged",
        title="美联储宣布加息25个基点",
        entity_ids=[],
        document_ids=[],
        importance=0.92,
        urgency="high",
        occurred_at=datetime.now(timezone.utc),
        key_fields={"policy_body": "federal_reserve", "rate_decision": "hike"},
        missing_required=[],
    )
    repo.save_event(event)
    return event


def test_worker_processes_pending_outbox_message() -> None:
    repo = InMemoryRepository()
    event = _save_event(repo)
    repo.add_outbox(
        "impact_analysis.requested.v1",
        event.id,
        {"event_id": event.id, "trigger": "test"},
    )

    processed = ImpactAnalysisWorker(repo, settings=_settings()).run_once(batch_size=10)

    assert processed == 1
    latest = repo.get_latest_impact_analysis_for_event(event.id)
    assert latest is not None
    assert latest.event_id == event.id


def test_worker_skips_non_impact_messages() -> None:
    repo = InMemoryRepository()
    event = _save_event(repo)
    repo.add_outbox(
        "fact_card.created.v1",
        event.id,
        {"event_id": event.id},
    )

    processed = ImpactAnalysisWorker(repo, settings=_settings()).run_once(batch_size=10)

    assert processed == 0
    assert repo.get_latest_impact_analysis_for_event(event.id) is None


def test_worker_skips_already_published_messages() -> None:
    repo = InMemoryRepository()
    event = _save_event(repo)
    repo.add_outbox(
        "impact_analysis.requested.v1",
        event.id,
        {"event_id": event.id},
    )
    messages = repo.list_pending_outbox(limit=10)
    repo.mark_outbox_published(messages[0].id, datetime.now(timezone.utc))

    processed = ImpactAnalysisWorker(repo, settings=_settings()).run_once(batch_size=10)

    assert processed == 0


def test_worker_handles_missing_event_id() -> None:
    repo = InMemoryRepository()
    event = _save_event(repo)
    repo.add_outbox(
        "impact_analysis.requested.v1",
        event.id,
        {},
    )

    processed = ImpactAnalysisWorker(repo, settings=_settings()).run_once(batch_size=10)

    assert processed == 0
    messages = repo.list_pending_outbox(limit=10)
    assert len(messages) == 0


class _FailingService:
    def generate(self, event_id: str, actor: str | None = None) -> ImpactAnalysis:
        raise RuntimeError("llm unavailable")


def test_worker_marks_failed_after_max_attempts() -> None:
    repo = InMemoryRepository()
    event = _save_event(repo)
    repo.add_outbox(
        "impact_analysis.requested.v1",
        event.id,
        {"event_id": event.id},
    )

    worker = ImpactAnalysisWorker(repo, settings=_settings(), service=_FailingService())
    for attempt in range(1, MAX_ATTEMPTS + 1):
        worker.run_once(batch_size=10)
        # 绕过指数退避，让消息在下次轮询中可见
        msg = repo.outbox[0]
        msg["next_attempt_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert msg["attempts"] == attempt

    # 第 MAX_ATTEMPTS + 1 次应进入死信
    worker.run_once(batch_size=10)

    messages = repo.list_outbox(limit=10)
    assert len(messages) == 1
    assert messages[0].dead_lettered_at is not None
