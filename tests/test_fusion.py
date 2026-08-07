from datetime import datetime, timezone

import pytest

from app.domain import CitationCandidate, FusionConfig, RetrievedItem
from app.retrieval.fusion import FusionService


def _item(
    chunk_id: str,
    score: float,
    backend: str,
    text: str = "摘录",
    document_id: str = "doc_001",
) -> RetrievedItem:
    return RetrievedItem(
        chunk_id=chunk_id,
        document_id=document_id,
        source_tier="S",
        chunk_type="event_description",
        text=text,
        score=score,
        citation=CitationCandidate(
            document_id=document_id,
            chunk_id=chunk_id,
            excerpt=text,
        ),
        backend=backend,
        backend_scores={backend: score},
    )


def test_fuse_rrf_combines_backends() -> None:
    service = FusionService()
    vector_items = [
        _item("chk_001", 0.9, "vector"),
        _item("chk_002", 0.8, "vector"),
    ]
    lexical_items = [
        _item("chk_002", 2.0, "lexical"),
        _item("chk_003", 1.0, "lexical"),
    ]

    fused, trace = service.fuse(
        {"vector": vector_items, "lexical": lexical_items},
        FusionConfig(method="rrf"),
    )

    assert len(fused) == 3
    assert trace["method"] == "rrf"
    assert trace["backend_coverage"] == {"vector": 2, "lexical": 2}
    # chk_002 在两路均出现，RRF 分数应最高。
    assert fused[0].chunk_id == "chk_002"
    assert fused[0].backend == "hybrid"
    assert "vector" in fused[0].backend_scores
    assert "lexical" in fused[0].backend_scores


def test_fuse_weighted_method() -> None:
    service = FusionService()
    vector_items = [_item("chk_001", 1.0, "vector"), _item("chk_002", 0.5, "vector")]
    lexical_items = [_item("chk_002", 1.0, "lexical"), _item("chk_003", 0.5, "lexical")]

    fused, _ = service.fuse(
        {"vector": vector_items, "lexical": lexical_items},
        FusionConfig(
            method="weighted",
            weights={"vector": 1.0, "lexical": 2.0},
        ),
    )

    # chk_002 同时命中两路，lexical 权重更高，应排第一。
    assert fused[0].chunk_id == "chk_002"


def test_fuse_context_policy_max_items() -> None:
    service = FusionService()
    vector_items = [_item(f"chk_{i:03d}", 1.0 - i * 0.1, "vector") for i in range(5)]

    fused, _ = service.fuse(
        {"vector": vector_items},
        FusionConfig(method="rrf", max_items=3),
    )

    assert len(fused) == 3
    assert fused[0].chunk_id == "chk_000"


def test_fuse_context_policy_diversity() -> None:
    service = FusionService()
    vector_items = [
        _item("chk_001", 0.9, "vector"),
        _item("chk_002", 0.8, "vector"),
    ]
    lexical_items = [
        _item("chk_002", 1.0, "lexical"),
        _item("chk_003", 0.7, "lexical"),
    ]

    fused, _ = service.fuse(
        {"vector": vector_items, "lexical": lexical_items},
        FusionConfig(
            method="rrf",
            diversity_min_backends=2,
        ),
    )

    assert all(len(item.backend_scores) >= 2 for item in fused)


def test_fuse_empty_backends_returns_empty() -> None:
    service = FusionService()
    fused, trace = service.fuse({}, FusionConfig())
    assert fused == []
    assert trace["backend_coverage"] == {}
