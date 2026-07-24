from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.domain import Document, EvidenceSpan, User
from app.main import create_app
from app.platform.repository import (
    DocumentNotSoftDeletedError,
    InMemoryRepository,
    PurgeRetentionWindowError,
    RetentionHoldError,
)


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
        published_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return Document(**base)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> EvidenceSpan:
    base = dict(
        id="evd-1",
        document_id="doc-1",
        revision_id="rev-1",
        locator={"block_id": "b1"},
        excerpt="excerpt",
        excerpt_hash="eh-1",
        locator_type="html_block",
        extraction_method="rule",
        extraction_version="v1",
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return EvidenceSpan(**base)  # type: ignore[arg-type]


def _admin_headers(client: TestClient) -> dict[str, str]:
    user = User(
        id="usr-admin",
        username="retention-admin",
        password_hash="unused",
        role="admin",
    )
    client.app.state.repository.save_user(user)
    token = client.app.state.token_manager.issue(user)
    return {"Authorization": f"Bearer {token}"}


def test_soft_delete_hides_document_and_evidence_from_default_reads() -> None:
    repo = InMemoryRepository()
    repo.save_document(_document())
    repo.save_evidence(_evidence())

    deleted = repo.soft_delete_document("doc-1")
    assert deleted.deleted_at is not None
    assert repo.get_document("doc-1") is None
    assert repo.get_document("doc-1", include_deleted=True) is not None
    assert repo.get_evidence("evd-1") is None
    assert repo.get_evidence("evd-1", include_deleted=True) is not None
    assert repo.get_evidence("evd-1", include_deleted=True).deleted_at is not None


def test_retention_hold_blocks_soft_delete() -> None:
    repo = InMemoryRepository()
    repo.save_document(_document())
    repo.set_document_retention_hold("doc-1", True)

    with pytest.raises(RetentionHoldError):
        repo.soft_delete_document("doc-1")

    assert repo.get_document("doc-1") is not None
    assert repo.get_document("doc-1").retention_hold is True


def test_soft_delete_is_idempotent() -> None:
    repo = InMemoryRepository()
    repo.save_document(_document())
    first = repo.soft_delete_document(
        "doc-1", deleted_at=datetime(2026, 7, 2, tzinfo=timezone.utc)
    )
    second = repo.soft_delete_document(
        "doc-1", deleted_at=datetime(2026, 7, 3, tzinfo=timezone.utc)
    )
    assert first.deleted_at == second.deleted_at


def test_delete_document_api_soft_deletes_and_audits() -> None:
    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        repository.save_document(_document(id="doc-api-1", external_id="ext-api-1"))
        repository.save_evidence(_evidence(id="evd-api-1", document_id="doc-api-1"))
        headers = _admin_headers(client)

        response = client.delete("/api/v1/documents/doc-api-1", headers=headers)
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["deleted"] is True
        assert body["id"] == "doc-api-1"
        assert body["deleted_at"]

        assert repository.get_document("doc-api-1") is None
        assert repository.get_evidence("evd-api-1") is None
        audits = [
            item
            for item in repository.list_audit_logs()
            if item.action == "document.soft_delete" and item.object_id == "doc-api-1"
        ]
        assert len(audits) == 1


def test_delete_document_api_retention_hold_returns_409() -> None:
    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        repository.save_document(_document(id="doc-hold-1", external_id="ext-hold-1"))
        repository.set_document_retention_hold("doc-hold-1", True)
        headers = _admin_headers(client)

        response = client.delete("/api/v1/documents/doc-hold-1", headers=headers)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RETENTION_HOLD"
        assert repository.get_document("doc-hold-1") is not None


def test_delete_document_api_missing_returns_404() -> None:
    with TestClient(create_app()) as client:
        headers = _admin_headers(client)
        response = client.delete("/api/v1/documents/doc-missing", headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


def test_patch_document_api_sets_retention_hold() -> None:
    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        repository.save_document(_document(id="doc-hold-api", external_id="ext-hold-api"))
        headers = _admin_headers(client)

        response = client.patch(
            "/api/v1/documents/doc-hold-api",
            headers=headers,
            json={"retention_hold": True},
        )
        assert response.status_code == 200
        assert response.json()["data"]["retention_hold"] is True
        assert repository.get_document("doc-hold-api").retention_hold is True

        release = client.patch(
            "/api/v1/documents/doc-hold-api",
            headers=headers,
            json={"retention_hold": False},
        )
        assert release.status_code == 200
        assert release.json()["data"]["retention_hold"] is False
        audits = [
            item
            for item in repository.list_audit_logs()
            if item.action == "document.retention_hold" and item.object_id == "doc-hold-api"
        ]
        assert len(audits) == 2


def test_patch_document_api_empty_body_returns_400() -> None:
    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        repository.save_document(_document(id="doc-empty", external_id="ext-empty"))
        headers = _admin_headers(client)
        response = client.patch("/api/v1/documents/doc-empty", headers=headers, json={})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "DOCUMENT_UPDATE_EMPTY"


def test_purge_document_requires_soft_delete_and_clears_content() -> None:
    repo = InMemoryRepository()
    repo.save_document(_document())
    repo.save_evidence(_evidence())

    with pytest.raises(DocumentNotSoftDeletedError):
        repo.purge_document("doc-1")

    repo.soft_delete_document("doc-1")
    purged = repo.purge_document("doc-1", min_soft_delete_age_seconds=0)
    assert purged.purged_at is not None
    assert purged.content == ""
    assert purged.title == "[purged]"
    assert repo.get_evidence("evd-1", include_deleted=True) is None
    again = repo.purge_document("doc-1", min_soft_delete_age_seconds=0)
    assert again.purged_at == purged.purged_at


def test_purge_document_blocked_by_retention_hold() -> None:
    repo = InMemoryRepository()
    repo.save_document(_document())
    repo.soft_delete_document("doc-1")
    # hold after soft-delete still blocks purge
    repo.set_document_retention_hold("doc-1", True)
    with pytest.raises(RetentionHoldError):
        repo.purge_document("doc-1", min_soft_delete_age_seconds=0)


def test_purge_document_respects_retention_window() -> None:
    repo = InMemoryRepository()
    repo.save_document(_document())
    deleted_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    repo.soft_delete_document("doc-1", deleted_at=deleted_at)

    with pytest.raises(PurgeRetentionWindowError):
        repo.purge_document(
            "doc-1",
            purged_at=deleted_at + timedelta(days=1),
            min_soft_delete_age_seconds=7 * 24 * 60 * 60,
        )

    purged = repo.purge_document(
        "doc-1",
        purged_at=deleted_at + timedelta(days=7),
        min_soft_delete_age_seconds=7 * 24 * 60 * 60,
    )
    assert purged.purged_at == deleted_at + timedelta(days=7)


def test_purge_document_api() -> None:
    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        repository.save_document(_document(id="doc-purge", external_id="ext-purge"))
        repository.save_evidence(_evidence(id="evd-purge", document_id="doc-purge"))
        headers = _admin_headers(client)

        not_ready = client.post("/api/v1/documents/doc-purge/purge", headers=headers)
        assert not_ready.status_code == 409
        assert not_ready.json()["error"]["code"] == "DOCUMENT_NOT_SOFT_DELETED"

        # Soft-delete with an old timestamp so the default 7d window has elapsed.
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        repository.soft_delete_document("doc-purge", deleted_at=old)

        # Fresh soft-delete should hit the window under default settings.
        repository.save_document(_document(id="doc-window", external_id="ext-window"))
        repository.soft_delete_document(
            "doc-window", deleted_at=datetime.now(timezone.utc)
        )
        window = client.post("/api/v1/documents/doc-window/purge", headers=headers)
        assert window.status_code == 409
        assert window.json()["error"]["code"] == "PURGE_RETENTION_WINDOW"

        response = client.post("/api/v1/documents/doc-purge/purge", headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["purged"] is True
        tombstone = repository.get_document("doc-purge", include_deleted=True)
        assert tombstone is not None
        assert tombstone.content == ""
        assert repository.get_evidence("evd-purge", include_deleted=True) is None
