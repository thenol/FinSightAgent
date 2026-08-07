from datetime import datetime, timezone

from app.document_intelligence.dedup import DisclosureGroupService
from app.document_intelligence.embeddings import EmbeddingService
from app.domain import Document, DocumentChunk
from app.platform.repository import InMemoryRepository


def _document(
    doc_id: str,
    title: str = "示例公告",
    content: str = "公司预计净利润增长。",
    external_id: str = "ext-001",
) -> Document:
    return Document(
        id=doc_id,
        source_id="src",
        source_tier="S",
        external_id=external_id,
        canonical_url=f"https://example.test/{external_id}",
        title=title,
        content=content,
        content_hash="hash",
        published_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )


_chunk_counter = 0


def _chunks(texts: list[str]) -> list[DocumentChunk]:
    global _chunk_counter
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    chunks = []
    offset = 0
    for text in texts:
        chunks.append(
            DocumentChunk(
                id=f"chk_{_chunk_counter}",
                block_id="blk_001",
                chunk_type="financial_impact",
                text=text,
                char_start=offset,
                char_end=offset + len(text),
                content_hash=f"hash_{text}",
                as_of=now,
                created_at=now,
            )
        )
        _chunk_counter += 1
        offset += len(text)
    return chunks


def test_new_document_creates_group() -> None:
    repository = InMemoryRepository()
    service = DisclosureGroupService(repository)
    document = _document("doc_001")
    chunks = _chunks(["公司预计净利润增长。"])

    group, membership, created = service.find_or_create_group(document, chunks)

    assert created is True
    assert membership.document_id == document.id
    assert membership.disclosure_group_id == group.id
    assert group.canonical_document_id == document.id


def test_same_content_from_different_source_joins_same_group() -> None:
    repository = InMemoryRepository()
    service = DisclosureGroupService(repository)
    title = "示例公司业绩预告"
    chunks = _chunks(["公司预计净利润同比增长20%。", "业绩期间为2026年半年度。"])

    first = _document("doc_001", title=title)
    group1, _, created1 = service.find_or_create_group(first, chunks)

    second = _document("doc_002", title=title, external_id="ext-002")
    group2, _, created2 = service.find_or_create_group(second, chunks)

    assert created1 is True
    assert created2 is False
    assert group1.id == group2.id
    assert len(repository.list_disclosure_group_members(group1.id)) == 2


def test_different_content_creates_separate_groups() -> None:
    repository = InMemoryRepository()
    service = DisclosureGroupService(repository)

    doc1 = _document("doc_001", title="公告A")
    chunks1 = _chunks(["公司预计净利润增长。"])

    doc2 = _document("doc_002", title="公告B")
    chunks2 = _chunks(["公司签订重大合同。"])

    group1, _, _ = service.find_or_create_group(doc1, chunks1)
    group2, _, _ = service.find_or_create_group(doc2, chunks2)

    assert group1.id != group2.id


def test_membership_is_idempotent() -> None:
    repository = InMemoryRepository()
    service = DisclosureGroupService(repository)
    document = _document("doc_001")
    chunks = _chunks(["公司预计净利润增长。"])

    service.find_or_create_group(document, chunks)
    service.find_or_create_group(document, chunks)

    group = repository.get_disclosure_group_for_document(document.id)
    assert group is not None
    assert len(repository.list_disclosure_group_members(group.id)) == 1


def test_semantically_similar_documents_join_same_group() -> None:
    """同一内容不同措辞应通过语义向量归入同一 DisclosureGroup。"""
    repository = InMemoryRepository()
    embedding_service = EmbeddingService(repository)
    service = DisclosureGroupService(repository, embedding_service=embedding_service)

    title = "示例公司业绩预告"
    first = _document("doc_001", title=title)
    chunks1 = _chunks(["公司预计净利润同比增长20%。"])
    group1, _, created1 = service.find_or_create_group(first, chunks1)

    second = _document("doc_002", title=title, external_id="ext-002")
    # 措辞不同但语义相同
    chunks2 = _chunks(["公司预计净利润较上年同期增长20%。"])
    group2, membership2, created2 = service.find_or_create_group(second, chunks2)

    assert created1 is True
    assert created2 is False
    assert group1.id == group2.id
    assert membership2.reason == "semantic_similarity"
    assert group2.representative_embedding is not None


def test_semantic_dedup_keeps_different_content_separate() -> None:
    repository = InMemoryRepository()
    embedding_service = EmbeddingService(repository)
    service = DisclosureGroupService(repository, embedding_service=embedding_service)

    doc1 = _document("doc_001", title="公告A")
    chunks1 = _chunks(
        ["公司预计2026年半年度归属于上市公司股东的净利润同比增长20%至30%。"]
    )

    doc2 = _document("doc_002", title="公告B")
    chunks2 = _chunks(
        ["公司近日与某客户签订重大销售合同，合同总金额约人民币1亿元。"]
    )

    group1, _, _ = service.find_or_create_group(doc1, chunks1)
    group2, _, _ = service.find_or_create_group(doc2, chunks2)

    assert group1.id != group2.id


def test_semantic_dedup_disabled_falls_back_to_exact_hash_only() -> None:
    repository = InMemoryRepository()
    embedding_service = EmbeddingService(repository)
    from app.document_intelligence.dedup import SemanticDedupConfig

    service = DisclosureGroupService(
        repository,
        embedding_service=embedding_service,
        semantic_config=SemanticDedupConfig(enabled=False),
    )

    title = "示例公司业绩预告"
    first = _document("doc_001", title=title)
    group1, _, _ = service.find_or_create_group(first, _chunks(["净利润增长20%。"]))

    second = _document("doc_002", title=title, external_id="ext-002")
    group2, _, _ = service.find_or_create_group(second, _chunks(["净利同比增长20%。"]))

    assert group1.id != group2.id


def test_find_similar_disclosure_groups_returns_scored_candidates() -> None:
    repository = InMemoryRepository()
    embedding_service = EmbeddingService(repository)
    service = DisclosureGroupService(repository, embedding_service=embedding_service)

    doc1 = _document("doc_001", title="A")
    service.find_or_create_group(doc1, _chunks(["公司预计净利润同比增长20%。"]))

    doc2 = _document("doc_002", title="B")
    service.find_or_create_group(doc2, _chunks(["公司签订重大合同金额1亿元。"]))

    query_chunks = _chunks(["公司预计净利润较上年同期增长20%。"])
    query_embedding = embedding_service.representative_embedding(query_chunks)
    assert query_embedding is not None

    results = repository.find_similar_disclosure_groups(
        query_embedding, "deterministic-embedding-v1", top_k=10
    )

    assert len(results) == 2
    # 与净利润相关的组应排在前面
    assert results[0][1] > results[1][1]
