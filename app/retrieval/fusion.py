"""多路检索融合：归一化、RRF/加权融合、去重与上下文预算裁剪。"""

from typing import Any, Optional

from app.domain import FusionConfig, RetrievedItem


def _normalize_scores(items: list[RetrievedItem]) -> list[float]:
    """对单路结果做 min-max 归一化；单元素或全零时返回原分数副本。"""
    if not items:
        return []
    scores = [item.score for item in items]
    min_score = min(scores)
    max_score = max(scores)
    if max_score == min_score:
        return scores[:]
    return [(s - min_score) / (max_score - min_score) for s in scores]


def _deduplicate(
    backend_items: dict[str, list[RetrievedItem]],
    backend_norm_scores: dict[str, list[float]],
) -> dict[str, dict[str, Any]]:
    """按 chunk_id 聚合各路结果，保留每路原始分和归一化分。"""
    merged: dict[str, dict[str, Any]] = {}
    for backend, items in backend_items.items():
        norm_scores = backend_norm_scores.get(backend, [])
        for rank, item in enumerate(items):
            norm = norm_scores[rank] if rank < len(norm_scores) else item.score
            record = merged.setdefault(
                item.chunk_id,
                {
                    "item": item,
                    "backends": set(),
                    "backend_scores": {},
                    "backend_norm_scores": {},
                    "ranks": {},
                },
            )
            record["backends"].add(backend)
            record["backend_scores"][backend] = item.score
            record["backend_norm_scores"][backend] = norm
            # 保留该路最佳排名（最小 rank）。
            if backend not in record["ranks"] or rank < record["ranks"][backend]:
                record["ranks"][backend] = rank
    return merged


def _apply_rrf(merged: dict[str, dict[str, Any]], rrf_k: int) -> list[RetrievedItem]:
    """使用 Reciprocal Rank Fusion 计算 fused score 并排序。"""
    fused: list[RetrievedItem] = []
    for record in merged.values():
        score = sum(1.0 / (rrf_k + rank + 1) for rank in record["ranks"].values())
        item = record["item"]
        fused.append(
            RetrievedItem(
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                source_tier=item.source_tier,
                chunk_type=item.chunk_type,
                text=item.text,
                score=round(score, 6),
                citation=item.citation,
                backend="hybrid",
                backend_scores=dict(record["backend_scores"]),
                embedding_model_version=item.embedding_model_version,
                retrieved_at=item.retrieved_at,
            )
        )
    fused.sort(key=lambda item: item.score, reverse=True)
    return fused


def _apply_weighted(
    merged: dict[str, dict[str, Any]],
    weights: dict[str, float],
) -> list[RetrievedItem]:
    """使用加权归一化分数融合并排序。"""
    fused: list[RetrievedItem] = []
    for record in merged.values():
        score = 0.0
        for backend, norm in record["backend_norm_scores"].items():
            score += weights.get(backend, 1.0) * norm
        item = record["item"]
        fused.append(
            RetrievedItem(
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                source_tier=item.source_tier,
                chunk_type=item.chunk_type,
                text=item.text,
                score=round(score, 6),
                citation=item.citation,
                backend="hybrid",
                backend_scores=dict(record["backend_scores"]),
                embedding_model_version=item.embedding_model_version,
                retrieved_at=item.retrieved_at,
            )
        )
    fused.sort(key=lambda item: item.score, reverse=True)
    return fused


def _estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 字符按 1 token，其它按空格分词。"""
    cjk_count = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    words = len([w for w in text.split() if w])
    return cjk_count + words


def _apply_context_policy(
    items: list[RetrievedItem],
    config: FusionConfig,
) -> list[RetrievedItem]:
    """按 max_items / max_tokens / diversity_min_backends 裁剪。"""
    result = items

    # 来源多样性：优先保留被多路同时命中的结果。
    if config.diversity_min_backends is not None:
        result = [
            item
            for item in result
            if len(item.backend_scores) >= config.diversity_min_backends
        ]

    if config.max_tokens is not None:
        kept: list[RetrievedItem] = []
        total = 0
        for item in result:
            cost = _estimate_tokens(item.text)
            if total + cost > config.max_tokens:
                break
            kept.append(item)
            total += cost
        result = kept

    if config.max_items is not None:
        result = result[: config.max_items]

    return result


class FusionService:
    """融合多路检索候选集，输出统一排序结果。"""

    def fuse(
        self,
        backend_items: dict[str, list[RetrievedItem]],
        config: Optional[FusionConfig] = None,
    ) -> tuple[list[RetrievedItem], dict[str, Any]]:
        """融合多路结果并返回 (items, trace_metadata)。"""
        config = config or FusionConfig()
        # 过滤空路。
        backend_items = {
            backend: items for backend, items in backend_items.items() if items
        }
        if not backend_items:
            return [], {"method": config.method, "backend_coverage": {}}

        backend_norm_scores = {
            backend: _normalize_scores(items)
            for backend, items in backend_items.items()
        }
        merged = _deduplicate(backend_items, backend_norm_scores)

        if config.method == "weighted":
            fused = _apply_weighted(merged, config.weights)
        else:
            fused = _apply_rrf(merged, config.rrf_k)

        fused = _apply_context_policy(fused, config)

        backend_coverage = {
            backend: len(items) for backend, items in backend_items.items()
        }
        trace = {
            "method": config.method,
            "rrf_k": config.rrf_k,
            "weights": dict(config.weights),
            "deduplicated_count": len(merged),
            "backend_coverage": backend_coverage,
        }
        return fused, trace
