from datetime import datetime, timedelta, timezone

from app.domain import Document
from app.ingestion.scheduler import build_source_scheduler
from app.platform.repository import InMemoryRepository
from app.platform.retention import purge_expired_documents
from app.platform.settings import Settings

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def _document(**overrides: object) -> Document:
    base = dict(
        id="doc-1",
        source_id="src-1",
        source_tier="A",
        external_id="ext-1",
        canonical_url="https://example.com/a",
        title="t",
        content="body",
        content_hash="hash-1",
        published_at=NOW - timedelta(days=10),
        ingested_at=NOW - timedelta(days=10),
        deleted_at=NOW - timedelta(days=8),
        retention_hold=False,
    )
    base.update(overrides)
    return Document(**base)  # type: ignore[arg-type]


def test_purge_expired_documents_purges_eligible_only() -> None:
    repo = InMemoryRepository()
    repo.save_document(_document(id="doc-old", external_id="old"))
    repo.save_document(
        _document(
            id="doc-hold",
            external_id="hold",
            retention_hold=True,
            deleted_at=NOW - timedelta(days=8),
        )
    )
    repo.save_document(
        _document(
            id="doc-fresh",
            external_id="fresh",
            deleted_at=NOW - timedelta(hours=1),
        )
    )
    repo.save_document(
        _document(
            id="doc-active",
            external_id="active",
            deleted_at=None,
        )
    )

    result = purge_expired_documents(
        repo,
        min_soft_delete_age_seconds=7 * 24 * 60 * 60,
        now=NOW,
        limit=50,
    )
    assert result["purged"] == 1
    assert result["purged_ids"] == ["doc-old"]
    assert repo.get_document("doc-old", include_deleted=True).content == ""
    assert repo.get_document("doc-old", include_deleted=True).purged_at == NOW
    assert repo.get_document("doc-hold", include_deleted=True).content == "body"
    assert repo.get_document("doc-fresh", include_deleted=True).content == "body"
    assert repo.get_document("doc-active") is not None


def test_scheduler_registers_auto_purge_job() -> None:
    repo = InMemoryRepository()
    settings = Settings(
        environment="development",
        repository="memory",
        database_url="postgresql+psycopg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        artifact_root=".data/artifacts",
        jwt_secret="x" * 32,
        bootstrap_admin_username="",
        bootstrap_admin_password="",
        document_purge_interval_seconds=120,
    )
    scheduler = build_source_scheduler(repo, sync_service=None, settings=settings)  # type: ignore[arg-type]
    job = scheduler.get_job("retention:auto_purge")
    assert job is not None
    assert int(job.trigger.interval.total_seconds()) == 120


def test_scheduler_skips_auto_purge_when_interval_zero() -> None:
    repo = InMemoryRepository()
    settings = Settings(
        environment="development",
        repository="memory",
        database_url="postgresql+psycopg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        artifact_root=".data/artifacts",
        jwt_secret="x" * 32,
        bootstrap_admin_username="",
        bootstrap_admin_password="",
        document_purge_interval_seconds=0,
    )
    scheduler = build_source_scheduler(repo, sync_service=None, settings=settings)  # type: ignore[arg-type]
    assert scheduler.get_job("retention:auto_purge") is None
