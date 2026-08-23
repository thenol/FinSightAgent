from datetime import datetime, timezone

from app.document_intelligence.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingService,
    cosine_similarity,
    mean_vector,
)
from app.domain import DocumentChunk
from app.platform.repository import InMemoryRepository


def _chunk(text: str, chunk_id: str = "chk_001") -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        block_id="blk_001",
        chunk_type="financial_impact",
        text=text,
        char_start=0,
        char_end=len(text),
        content_hash=f"hash_{text}",
        as_of=datetime(2026, 7, 12, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )


def test_cosine_similarity_of_identical_vectors_is_one() -> None:
    vector = [1.0, 0.0, 0.0]
    assert cosine_similarity(vector, vector) == 1.0


def test_cosine_similarity_of_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_mean_vector_of_same_vectors_is_same_direction() -> None:
    vector = [1.0, 0.0]
    result = mean_vector([vector, vector])
    assert result == [1.0, 0.0]


def test_mean_vector_is_normalized() -> None:
    result = mean_vector([[1.0, 0.0], [0.0, 1.0]])
    assert round(result[0], 6) == round(result[1], 6)


def test_deterministic_provider_produces_unit_vectors() -> None:
    provider = DeterministicEmbeddingProvider(dimension=32)
    vectors = provider.embed(["净利润增长", "营业收入下降"])
    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector) == 32
        # 单位向量模长接近 1（浮点误差可接受）
        assert abs(sum(v * v for v in vector) - 1.0) < 1e-5


def test_deterministic_provider_default_dimension_matches_openai() -> None:
    provider = DeterministicEmbeddingProvider()
    vectors = provider.embed(["测试"])
    assert len(vectors[0]) == 1536


def test_similar_texts_have_higher_similarity() -> None:
    provider = DeterministicEmbeddingProvider(dimension=64)
    vectors = provider.embed(
        [
            "公司预计净利润同比增长20%",
            "公司预计净利润同比增长20%",
            "公司签订重大合同金额1亿元",
        ]
    )
    same = cosine_similarity(vectors[0], vectors[1])
    different = cosine_similarity(vectors[0], vectors[2])
    assert same > different
    assert same == 1.0


def test_embedding_service_creates_records() -> None:
    repository = InMemoryRepository()
    service = EmbeddingService(repository)
    chunks = [
        _chunk("净利润增长", "chk_a"),
        _chunk("营收下降", "chk_b"),
    ]

    records = service.embed_chunks(chunks)

    assert len(records) == 2
    for record in records:
        assert record.status == "completed"
        assert record.embedding
        assert record.embedding_model_version == "deterministic-embedding-v1"


def test_embedding_service_reuses_existing_records() -> None:
    repository = InMemoryRepository()
    service = EmbeddingService(repository)
    chunks = [_chunk("净利润增长", "chk_a")]

    first = service.embed_chunks(chunks)
    second = service.embed_chunks(chunks)

    assert first[0].id == second[0].id


def test_representative_embedding_combines_chunks() -> None:
    repository = InMemoryRepository()
    service = EmbeddingService(repository)
    chunks = [
        _chunk("净利润增长", "chk_a"),
        _chunk("营收下降", "chk_b"),
    ]

    vector = service.representative_embedding(chunks)

    assert vector is not None
    assert len(vector) == 1536


def test_embedding_service_returns_failed_records_on_provider_error() -> None:
    class BrokenProvider:
        model_version = "broken-v1"
        dimension = 8

        def embed(self, texts: list[str]) -> list[list[float]]:
            from app.document_intelligence.embeddings import EmbeddingProviderError

            raise EmbeddingProviderError("BROKEN")

    repository = InMemoryRepository()
    service = EmbeddingService(repository, provider=BrokenProvider())
    chunks = [_chunk("净利润增长", "chk_a")]

    records = service.embed_chunks(chunks)

    assert len(records) == 1
    assert records[0].status == "failed"
    assert records[0].error_code == "BROKEN"
    assert records[0].embedding == []
