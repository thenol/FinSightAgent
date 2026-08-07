from datetime import datetime, timezone

from app.document_intelligence.embeddings import EmbeddingService
from app.domain import Document, DocumentBlock, DocumentChunk, DocumentRevision, RetrievalRequest
from app.platform.repository import InMemoryRepository
from app.retrieval.service import RetrievalService


def _seed(repository: InMemoryRepository) -> tuple[Document, DocumentBlock, DocumentChunk]:
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    document = Document(
        id="doc_001",
        source_id="src_s",
        source_tier="S",
        external_id="ext-001",
        canonical_url="https://example.test/001",
        title="业绩预告",
        content="公司预计净利润同比增长20%。",
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
        text="公司预计净利润同比增长20%。",
        char_start=0,
        char_end=len("公司预计净利润同比增长20%。"),
        order_index=0,
        created_at=now,
    )
    chunk = DocumentChunk(
        id="chk_001",
        block_id=block.id,
        chunk_type="financial_impact",
        text="公司预计净利润同比增长20%。",
        char_start=0,
        char_end=len("公司预计净利润同比增长20%。"),
        content_hash="hash_chunk_001",
        as_of=now,
        created_at=now,
    )
    parsed = {
        "id": "pd_001",
        "document_id": document.id,
        "revision_id": revision.id,
        "parser_version": "v1",
        "parser_run_id": "run_001",
        "language": "zh",
        "title": "业绩预告",
        "block_ids": [block.id],
        "created_at": now,
    }
    from app.domain import ParsedDocument

    repository.documents[document.id] = document
    repository.revisions[revision.id] = revision
    repository.parsed_documents[parsed["id"]] = ParsedDocument(**parsed)
    repository.document_blocks[block.id] = block
    repository.document_chunks[chunk.id] = chunk
    return document, block, chunk


def test_retrieve_returns_similar_chunks() -> None:
    repository = InMemoryRepository()
    document, block, chunk = _seed(repository)

    # 预生成 chunk embedding
    EmbeddingService(repository).embed_chunks([chunk])

    service = RetrievalService(repository)
    request = RetrievalRequest(query="净利润增长", top_k=5)
    trace = service.retrieve(request)

    assert trace.candidate_count >= 1
    assert len(trace.items) >= 1
    item = trace.items[0]
    assert item.chunk_id == chunk.id
    assert item.document_id == document.id
    assert item.source_tier == "S"
    assert item.citation.document_id == document.id
    assert item.citation.chunk_id == chunk.id


def test_retrieve_filters_by_chunk_type() -> None:
    repository = InMemoryRepository()
    document, block, chunk = _seed(repository)
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    other_chunk = DocumentChunk(
        id="chk_002",
        block_id=block.id,
        chunk_type="background",
        text="公司成立于2000年。",
        char_start=0,
        char_end=len("公司成立于2000年。"),
        content_hash="hash_chunk_002",
        as_of=now,
        created_at=now,
    )
    repository.document_chunks[other_chunk.id] = other_chunk

    EmbeddingService(repository).embed_chunks([chunk, other_chunk])

    service = RetrievalService(repository)
    trace = service.retrieve(
        RetrievalRequest(query="净利润增长", chunk_types=["financial_impact"])
    )

    assert all(item.chunk_type == "financial_impact" for item in trace.items)


def test_retrieve_filters_by_source_tier() -> None:
    repository = InMemoryRepository()
    document, block, chunk = _seed(repository)
    document_tier_a = Document(
        id="doc_002",
        source_id="src_a",
        source_tier="A",
        external_id="ext-002",
        canonical_url="https://example.test/002",
        title="媒体报道",
        content="公司预计净利润同比增长20%。",
        content_hash="hash_002",
        published_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )
    repository.documents[document_tier_a.id] = document_tier_a

    service = RetrievalService(repository)
    trace = service.retrieve(RetrievalRequest(query="净利润增长", source_tiers=["S"]))

    assert all(item.source_tier == "S" for item in trace.items)


def test_retrieve_respects_as_of() -> None:
    repository = InMemoryRepository()
    document, block, chunk = _seed(repository)

    EmbeddingService(repository).embed_chunks([chunk])

    service = RetrievalService(repository)
    trace = service.retrieve(
        RetrievalRequest(
            query="净利润增长",
            as_of=datetime(2026, 7, 11, tzinfo=timezone.utc),
        )
    )

    assert trace.candidate_count == 0
    assert trace.items == []


def test_retrieve_lexical_returns_matching_chunks() -> None:
    repository = InMemoryRepository()
    document, block, chunk = _seed(repository)

    service = RetrievalService(repository)
    trace = service.retrieve(
        RetrievalRequest(query="净利润", retrieval_mode="lexical", top_k=5)
    )

    assert trace.candidate_count >= 1
    assert len(trace.items) >= 1
    item = trace.items[0]
    assert item.chunk_id == chunk.id
    assert item.document_id == document.id
    assert item.source_tier == "S"
    assert item.embedding_model_version == ""
    assert item.citation.document_id == document.id


def test_retrieve_lexical_filters_by_chunk_type() -> None:
    repository = InMemoryRepository()
    document, block, chunk = _seed(repository)
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    other_chunk = DocumentChunk(
        id="chk_002",
        block_id=block.id,
        chunk_type="background",
        text="公司成立于2000年。",
        char_start=0,
        char_end=len("公司成立于2000年。"),
        content_hash="hash_chunk_002",
        as_of=now,
        created_at=now,
    )
    repository.document_chunks[other_chunk.id] = other_chunk

    service = RetrievalService(repository)
    trace = service.retrieve(
        RetrievalRequest(
            query="公司",
            retrieval_mode="lexical",
            chunk_types=["financial_impact"],
        )
    )

    assert all(item.chunk_type == "financial_impact" for item in trace.items)


def test_retrieve_lexical_filters_by_source_tier() -> None:
    repository = InMemoryRepository()
    document, block, chunk = _seed(repository)
    document_tier_a = Document(
        id="doc_002",
        source_id="src_a",
        source_tier="A",
        external_id="ext-002",
        canonical_url="https://example.test/002",
        title="媒体报道",
        content="公司预计净利润同比增长20%。",
        content_hash="hash_002",
        published_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )
    repository.documents[document_tier_a.id] = document_tier_a

    service = RetrievalService(repository)
    trace = service.retrieve(
        RetrievalRequest(query="净利润", retrieval_mode="lexical", source_tiers=["S"])
    )

    assert all(item.source_tier == "S" for item in trace.items)


def test_retrieve_lexical_respects_as_of() -> None:
    repository = InMemoryRepository()
    document, block, chunk = _seed(repository)

    service = RetrievalService(repository)
    trace = service.retrieve(
        RetrievalRequest(
            query="净利润",
            retrieval_mode="lexical",
            as_of=datetime(2026, 7, 11, tzinfo=timezone.utc),
        )
    )

    assert trace.candidate_count == 0
    assert trace.items == []


def test_retrieve_hybrid_combines_vector_and_lexical() -> None:
    repository = InMemoryRepository()
    document, block, chunk = _seed(repository)

    # 预生成 embedding，使 vector 路能召回同一 chunk。
    EmbeddingService(repository).embed_chunks([chunk])

    service = RetrievalService(repository)
    trace = service.retrieve(
        RetrievalRequest(query="净利润", retrieval_mode="hybrid", top_k=3)
    )

    assert trace.fusion_method == "rrf"
    assert "vector" in trace.backend_coverage
    assert "lexical" in trace.backend_coverage
    assert len(trace.items) >= 1
    item = trace.items[0]
    assert item.chunk_id == chunk.id
    assert item.backend == "hybrid"
    assert "vector" in item.backend_scores
    assert "lexical" in item.backend_scores
