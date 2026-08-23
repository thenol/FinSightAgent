"""PostgreSQL / pgvector path tests. Skipped unless FINSIGHT_POSTGRES_TEST_URL is set."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.application.pipeline import EventResearchPipeline
from app.domain import (
    Document,
    DocumentBlock,
    DocumentChunk,
    EmbeddingRecord,
    ParsedDocument,
)
from app.platform.db_models import EMBEDDING_DIMENSION
from app.platform.ids import new_id
from app.platform.repository import SqlAlchemyRepository

postgres_url = os.getenv("FINSIGHT_POSTGRES_TEST_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not postgres_url,
        reason="FINSIGHT_POSTGRES_TEST_URL is not set",
    ),
]


@pytest.fixture(scope="module")
def postgres_repository() -> SqlAlchemyRepository:
    assert postgres_url is not None
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_url)
    os.environ["FINSIGHT_DATABASE_URL"] = postgres_url
    command.upgrade(config, "head")
    return SqlAlchemyRepository(postgres_url)


def test_postgres_pipeline_round_trip(postgres_repository: SqlAlchemyRepository) -> None:
    suffix = new_id("pg")
    created = EventResearchPipeline(postgres_repository).process(
        idempotency_key=f"pg-{suffix}",
        source_id="sse",
        source_tier="S",
        external_id=f"sse-{suffix}",
        url=f"https://example.test/{suffix}",
        title="示例公司（600000.SH）重大合同公告",
        content="公司与客户签署重大合同，合同金额为人民币1亿元。",
        published_at=datetime(2026, 7, 12, 1, 30, tzinfo=timezone.utc),
    )
    reloaded = postgres_repository.get_event(created.event.id)
    assert reloaded is not None
    assert reloaded.event_type == "major_contract"
    assert reloaded.time_resolution.get("resolution_method")


def test_postgres_pgvector_ranks_nearest_chunk(
    postgres_repository: SqlAlchemyRepository,
) -> None:
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    document = Document(
        id=new_id("doc"),
        source_id="sse",
        source_tier="S",
        external_id=new_id("ext"),
        canonical_url="https://example.test/vector",
        title="vector",
        content="净利润同比增长",
        content_hash=new_id("hsh"),
        published_at=now,
        ingested_at=now,
    )
    parsed = ParsedDocument(
        id=new_id("prs"),
        document_id=document.id,
        revision_id=new_id("rev"),
        parser_version="test",
        parser_run_id=new_id("run"),
        language="zh",
        title=document.title,
        block_ids=[],
        created_at=now,
    )
    block = DocumentBlock(
        id=new_id("blk"),
        parsed_document_id=parsed.id,
        revision_id=parsed.revision_id,
        block_type="paragraph",
        block_id="p1",
        text=document.content,
        char_start=0,
        char_end=len(document.content),
        order_index=0,
        created_at=now,
    )
    near = DocumentChunk(
        id=new_id("chk"),
        block_id=block.id,
        chunk_type="financial_impact",
        text="净利润同比增长",
        char_start=0,
        char_end=7,
        content_hash="near",
        as_of=now,
        created_at=now,
    )
    far = DocumentChunk(
        id=new_id("chk"),
        block_id=block.id,
        chunk_type="background",
        text="无关背景",
        char_start=0,
        char_end=4,
        content_hash="far",
        as_of=now,
        created_at=now,
    )
    query = [0.0] * EMBEDDING_DIMENSION
    query[0] = 1.0
    near_vector = list(query)
    far_vector = [0.0] * EMBEDDING_DIMENSION
    far_vector[1] = 1.0
    with postgres_repository.transaction() as tx:
        tx.save_document(document)
        tx.save_parsed_document(parsed)
        tx.save_document_block(block)
        tx.save_document_chunk(near)
        tx.save_document_chunk(far)
        tx.save_embedding_record(
            EmbeddingRecord(
                id=new_id("emb"),
                chunk_id=near.id,
                embedding_model_version="test-v1",
                embedding=near_vector,
                content_hash="near",
                status="completed",
                created_at=now,
            )
        )
        tx.save_embedding_record(
            EmbeddingRecord(
                id=new_id("emb"),
                chunk_id=far.id,
                embedding_model_version="test-v1",
                embedding=far_vector,
                content_hash="far",
                status="completed",
                created_at=now,
            )
        )

    ranked = postgres_repository.find_similar_document_chunks(
        query, "test-v1", top_k=2
    )
    assert ranked
    assert ranked[0][0].id == near.id
    assert ranked[0][1] > ranked[1][1]


def test_postgres_api_ingest_round_trip(
    postgres_repository: SqlAlchemyRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "postgresql")
    monkeypatch.setenv("FINSIGHT_DATABASE_URL", postgres_url or "")
    monkeypatch.setenv("FINSIGHT_ENV", "test")
    monkeypatch.setenv("FINSIGHT_JWT_SECRET", "ci-postgres-secret-that-is-32bytes")
    monkeypatch.setenv("FINSIGHT_BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("FINSIGHT_BOOTSTRAP_ADMIN_PASSWORD", "admin123")
    monkeypatch.setenv("FINSIGHT_WORKFLOW_AUTO_TRIGGER_ENABLED", "false")
    from app.main import create_app

    suffix = new_id("api")
    with TestClient(create_app()) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert login.status_code == 200
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": suffix}
        ingested = client.post(
            "/api/v1/documents/ingest",
            headers=headers,
            json={
                "source_id": "sse",
                "source_tier": "S",
                "external_id": f"sse-{suffix}",
                "url": f"https://example.test/{suffix}",
                "title": "示例公司（600000.SH）重大合同公告",
                "content": "公司与客户签署重大合同，合同金额为人民币1亿元。",
                "published_at": "2026-07-12T09:30:00+08:00",
            },
        )
        assert ingested.status_code == 201
        event_id = ingested.json()["data"]["event_id"]
        detail = client.get(f"/api/v1/events/{event_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["data"]["event_type"] == "major_contract"
