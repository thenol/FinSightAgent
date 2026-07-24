"""高重要度事件自动触发研究工作流的验收。"""

from datetime import datetime, timezone

import pytest

from app.application.pipeline import EventResearchPipeline
from app.platform.repository import InMemoryRepository
from app.platform.settings import Settings


def _payload(importance_boost: bool = True) -> dict:
    content = "公司预计2026年半年度净利润同比增长20%至30%。"
    source_tier = "C"
    published_at = datetime(2026, 5, 1, 1, 30, tzinfo=timezone.utc)
    if importance_boost:
        # S 级 + 当天 + 大业绩变化，使 importance 超过默认阈值 0.7
        content = "公司预计2026年半年度净利润同比增长500%至600%。"
        source_tier = "S"
        published_at = datetime(2026, 7, 12, 1, 30, tzinfo=timezone.utc)
    return {
        "source_id": "szse",
        "source_tier": source_tier,
        "external_id": "notice-001",
        "url": "https://example.test/notice?id=1",
        "title": "示例公司（000001.SZ）2026年半年度业绩预告",
        "content": content,
        "published_at": published_at,
    }


def _settings(enabled: bool = True, threshold: float = 0.7) -> Settings:
    return Settings(
        environment="test",
        repository="memory",
        database_url="postgresql+psycopg://x",
        redis_url="redis://localhost",
        artifact_root=".data/artifacts",
        jwt_secret="development-only-secret-change-me-32-bytes",
        bootstrap_admin_username="",
        bootstrap_admin_password="",
        workflow_auto_trigger_enabled=enabled,
        workflow_auto_importance_threshold=threshold,
    ).validate()


def test_high_importance_event_auto_creates_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINSIGHT_WORKFLOW_AUTO_TRIGGER_ENABLED", "true")
    repository = InMemoryRepository()
    settings = _settings(enabled=True, threshold=0.7)
    pipeline = EventResearchPipeline(repository, settings=settings)

    result = pipeline.process(idempotency_key="auto-1", **_payload(importance_boost=True))

    runs = repository.list_workflow_runs(event_id=result.event.id)
    assert len(runs) == 1
    assert runs[0].trigger_id == "auto"
    assert runs[0].status == "pending"
    assert runs[0].event_id == result.event.id
    assert result.event.importance >= 0.7


def test_low_importance_event_does_not_auto_create_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINSIGHT_WORKFLOW_AUTO_TRIGGER_ENABLED", "true")
    repository = InMemoryRepository()
    settings = _settings(enabled=True, threshold=0.7)
    pipeline = EventResearchPipeline(repository, settings=settings)

    result = pipeline.process(idempotency_key="auto-2", **_payload(importance_boost=False))

    assert result.event.importance < 0.7
    assert repository.list_workflow_runs(event_id=result.event.id) == []


def test_auto_trigger_disabled_does_not_create_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINSIGHT_WORKFLOW_AUTO_TRIGGER_ENABLED", "false")
    repository = InMemoryRepository()
    settings = _settings(enabled=False, threshold=0.7)
    pipeline = EventResearchPipeline(repository, settings=settings)

    result = pipeline.process(idempotency_key="auto-3", **_payload(importance_boost=True))

    assert result.event.importance >= 0.7
    assert repository.list_workflow_runs(event_id=result.event.id) == []


def test_duplicate_event_does_not_create_second_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINSIGHT_WORKFLOW_AUTO_TRIGGER_ENABLED", "true")
    repository = InMemoryRepository()
    settings = _settings(enabled=True, threshold=0.7)
    pipeline = EventResearchPipeline(repository, settings=settings)

    first = pipeline.process(idempotency_key="auto-4", **_payload(importance_boost=True))
    second = pipeline.process(idempotency_key="auto-4", **_payload(importance_boost=True))

    assert first.event.id == second.event.id
    runs = repository.list_workflow_runs(event_id=first.event.id)
    assert len(runs) == 1
