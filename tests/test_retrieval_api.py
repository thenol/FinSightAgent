from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.document_intelligence.embeddings import EmbeddingService
from app.domain import (
    Document,
    DocumentBlock,
    DocumentChunk,
    DocumentRevision,
    Entity,
    Event,
    ParsedDocument,
)
from app.main import create_app


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def _seed(repository: object) -> tuple[Document, DocumentBlock, DocumentChunk, Entity, Event]:
    now = datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc)
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
        document_ids=["doc_001"],
        importance=0.85,
        urgency="normal",
        occurred_at=now,
    )
    document = Document(
        id="doc_001",
        source_id="src_s",
        source_tier="S",
        external_id="ext-001",
        canonical_url="https://example.test/001",
        title="美联储宣布加息25个基点",
        content="美联储宣布将联邦基金利率目标区间上调25个基点。",
        content_hash="hash_001",
        published_at=now,
        ingested_at=now,
    )
    revision = DocumentRevision(
        id="rev_001",
        document_id=document.id,
        revision_no=1,
        artifact_id="art_001",
        content_hash="hash_001",
        normalized_content_uri="uri://001",
        parser_version="v1",
        created_at=now,
    )
    block = DocumentBlock(
        id="blk_001",
        parsed_document_id="pd_001",
        revision_id=revision.id,
        block_type="paragraph",
        block_id="p1",
        text="美联储宣布将联邦基金利率目标区间上调25个基点。",
        char_start=0,
        char_end=len("美联储宣布将联邦基金利率目标区间上调25个基点。"),
        order_index=0,
        created_at=now,
    )
    chunk = DocumentChunk(
        id="chk_001",
        block_id=block.id,
        chunk_type="event_description",
        text="美联储宣布将联邦基金利率目标区间上调25个基点。",
        char_start=0,
        char_end=len("美联储宣布将联邦基金利率目标区间上调25个基点。"),
        content_hash="hash_chunk_001",
        as_of=now,
        created_at=now,
    )
    parsed = ParsedDocument(
        id="pd_001",
        document_id=document.id,
        revision_id=revision.id,
        parser_version="v1",
        parser_run_id="run_001",
        language="zh",
        title=document.title,
        block_ids=[block.id],
        created_at=now,
    )
    repository.entities[entity.id] = entity
    repository.events[event.id] = event
    repository.documents[document.id] = document
    repository.revisions[revision.id] = revision
    repository.parsed_documents[parsed.id] = parsed
    repository.document_blocks[block.id] = block
    repository.document_chunks[chunk.id] = chunk
    return document, block, chunk, entity, event


def test_retrieve_api_vector_mode() -> None:
    app = create_app()
    with TestClient(app) as client:
        repository = app.state.repository
        _seed(repository)
        chunk = repository.document_chunks["chk_001"]
        EmbeddingService(repository).embed_chunks([chunk])

        token = _login(client)
        response = client.post(
            "/api/v1/retrieval/retrieve",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": "美联储加息", "retrieval_mode": "vector", "top_k": 5},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["candidate_count"] >= 1
        assert any(item["chunk_id"] == "chk_001" for item in data["items"])


def test_retrieve_api_graph_mode() -> None:
    app = create_app()
    with TestClient(app) as client:
        repository = app.state.repository
        _seed(repository)

        token = _login(client)
        response = client.post(
            "/api/v1/retrieval/retrieve",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": "美联储加息影响", "retrieval_mode": "graph", "top_k": 5},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["candidate_count"] >= 1
        assert any(item["backend"] == "graph" for item in data["items"])
        assert any(item["chunk_id"] == "chk_001" for item in data["items"])


def test_retrieve_api_sql_mode() -> None:
    app = create_app()
    with TestClient(app) as client:
        repository = app.state.repository
        _seed(repository)

        token = _login(client)
        response = client.post(
            "/api/v1/retrieval/retrieve",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "query": "macro_policy 事件",
                "retrieval_mode": "sql",
                "top_k": 5,
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert any(item["backend"] == "sql" for item in data["items"])
        assert any(item["chunk_id"] == "evt_001" for item in data["items"])


def test_retrieve_api_timeseries_mode() -> None:
    app = create_app()
    with TestClient(app) as client:
        repository = app.state.repository
        _seed(repository)

        token = _login(client)
        response = client.post(
            "/api/v1/retrieval/retrieve",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "query": "2026-08-01 到 2026-08-10 时间线",
                "retrieval_mode": "timeseries",
                "top_k": 5,
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert any(item["backend"] == "timeseries" for item in data["items"])


def test_retrieve_api_planned_mode() -> None:
    app = create_app()
    with TestClient(app) as client:
        repository = app.state.repository
        _seed(repository)
        chunk = repository.document_chunks["chk_001"]
        EmbeddingService(repository).embed_chunks([chunk])

        token = _login(client)
        response = client.post(
            "/api/v1/retrieval/retrieve",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "query": "美联储加息对银行的影响",
                "retrieval_mode": "planned",
                "top_k": 5,
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert "items" in data
        # planned 应融合 graph + vector + sql 至少一路有结果
        backends = {item["backend"] for item in data["items"]}
        assert bool(backends)
