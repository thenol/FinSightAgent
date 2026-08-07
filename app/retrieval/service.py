"""Hybrid Retrieval 服务：基于向量相似度、关键词或混合召回 DocumentChunk。"""

from datetime import datetime, timezone
from typing import Any, Optional

from app.document_intelligence.embeddings import EmbeddingService
from app.domain import (
    CitationCandidate,
    DocumentChunk,
    FusionConfig,
    RetrievalRequest,
    RetrievalTrace,
    RetrievedItem,
)
from app.platform.repository import Repository
from app.retrieval.fusion import FusionService
from app.retrieval.lexical import tokenize_keywords


class RetrievalService:
    """提供基于 Embedding、关键词或混合模式的 DocumentChunk 检索。"""

    def __init__(
        self,
        repository: Repository,
        embedding_service: Optional[EmbeddingService] = None,
        fusion_service: Optional[FusionService] = None,
    ) -> None:
        self.repository = repository
        self.embedding_service = embedding_service or EmbeddingService(repository)
        self.fusion_service = fusion_service or FusionService()

    def retrieve(self, request: RetrievalRequest) -> RetrievalTrace:
        """执行检索并返回带审计轨迹的结果。"""
        if request.retrieval_mode == "lexical":
            return self._retrieve_lexical(request)
        if request.retrieval_mode == "hybrid":
            return self._retrieve_hybrid(request)
        return self._retrieve_vector(request)

    def _retrieve_vector(self, request: RetrievalRequest) -> RetrievalTrace:
        """向量召回路径。"""
        now = datetime.now(timezone.utc)
        model_version = (
            request.embedding_model_version or self.embedding_service.provider.model_version
        )

        query_embedding = self._embed_query(request.query, model_version)
        filters = {
            "as_of": request.as_of.isoformat() if request.as_of else None,
            "chunk_types": request.chunk_types,
            "source_tiers": request.source_tiers,
        }
        if query_embedding is None:
            return self._empty_trace(request, model_version, filters, now)

        candidates = self.repository.find_similar_document_chunks(
            query_embedding,
            model_version,
            top_k=request.top_k,
            as_of=request.as_of,
            chunk_types=request.chunk_types,
            source_tiers=request.source_tiers,
        )

        items = [
            self._build_item(chunk, score, model_version, now, backend="vector")
            for chunk, score in candidates
        ]
        if request.min_score > 0:
            items = [item for item in items if item.score >= request.min_score]

        return RetrievalTrace(
            request=request,
            embedding_model_version=model_version,
            filters=filters,
            candidate_count=len(candidates),
            items=items,
            generated_at=now,
        )

    def _retrieve_lexical(self, request: RetrievalRequest) -> RetrievalTrace:
        """关键词召回路径。"""
        now = datetime.now(timezone.utc)
        keywords = tokenize_keywords(request.query)
        filters = {
            "as_of": request.as_of.isoformat() if request.as_of else None,
            "chunk_types": request.chunk_types,
            "source_tiers": request.source_tiers,
            "keywords": keywords,
        }
        if not keywords:
            return self._empty_trace(request, "", filters, now)

        candidates = self.repository.find_document_chunks_by_keywords(
            keywords,
            top_k=request.top_k,
            as_of=request.as_of,
            chunk_types=request.chunk_types,
            source_tiers=request.source_tiers,
        )

        items = [
            self._build_item(chunk, score, "", now, backend="lexical")
            for chunk, score in candidates
        ]
        if request.min_score > 0:
            items = [item for item in items if item.score >= request.min_score]

        return RetrievalTrace(
            request=request,
            embedding_model_version="",
            filters=filters,
            candidate_count=len(candidates),
            items=items,
            generated_at=now,
        )

    def _retrieve_hybrid(self, request: RetrievalRequest) -> RetrievalTrace:
        """混合召回路径：并行执行 vector + lexical，经 Fusion 后返回统一结果。"""
        now = datetime.now(timezone.utc)
        model_version = (
            request.embedding_model_version or self.embedding_service.provider.model_version
        )

        # 为各路预留更多候选，供 Fusion 阶段去重和重排。
        per_backend_top_k = max(request.top_k * 3, 10)

        vector_trace = self._retrieve_vector(
            RetrievalRequest(
                query=request.query,
                embedding_model_version=request.embedding_model_version,
                top_k=per_backend_top_k,
                as_of=request.as_of,
                chunk_types=request.chunk_types,
                source_tiers=request.source_tiers,
                min_score=0.0,
                retrieval_mode="vector",
            )
        )
        lexical_trace = self._retrieve_lexical(
            RetrievalRequest(
                query=request.query,
                top_k=per_backend_top_k,
                as_of=request.as_of,
                chunk_types=request.chunk_types,
                source_tiers=request.source_tiers,
                min_score=0.0,
                retrieval_mode="lexical",
            )
        )

        backend_items = {
            "vector": vector_trace.items,
            "lexical": lexical_trace.items,
        }
        fused_items, fusion_trace = self.fusion_service.fuse(
            backend_items,
            FusionConfig(
                method="rrf",
                max_items=request.top_k,
            ),
        )

        if request.min_score > 0:
            fused_items = [item for item in fused_items if item.score >= request.min_score]

        filters = {
            "as_of": request.as_of.isoformat() if request.as_of else None,
            "chunk_types": request.chunk_types,
            "source_tiers": request.source_tiers,
            "fusion": fusion_trace,
        }

        return RetrievalTrace(
            request=request,
            embedding_model_version=model_version,
            filters=filters,
            candidate_count=sum(len(items) for items in backend_items.values()),
            items=fused_items,
            fusion_method=fusion_trace["method"],
            backend_coverage=fusion_trace["backend_coverage"],
            generated_at=now,
        )

    def _embed_query(self, query: str, model_version: str) -> Optional[list[float]]:
        # 把查询文本当作一个临时 chunk 处理；EmbeddingService 会复用或生成 embedding。
        from app.domain import DocumentChunk

        temp_chunk = DocumentChunk(
            id="retrieval_query",
            block_id="retrieval_query",
            chunk_type="event_description",
            text=query,
            char_start=0,
            char_end=len(query),
            content_hash=f"query_{hash(query)}",
        )
        records = self.embedding_service.embed_chunks([temp_chunk], model_version=model_version)
        record = records[0]
        if record.status != "completed":
            return None
        return record.embedding

    def _build_item(
        self,
        chunk: DocumentChunk,
        score: float,
        model_version: str,
        now: datetime,
        backend: str = "vector",
    ) -> RetrievedItem:
        document_id = ""
        source_tier = ""
        block = self.repository.get_document_block(chunk.block_id)
        if block is not None:
            parsed = self.repository.get_parsed_document_by_revision(block.revision_id)
            if parsed is not None:
                document = self.repository.get_document(parsed.document_id)
                if document is not None:
                    document_id = document.id
                    source_tier = document.source_tier

        citation = CitationCandidate(
            document_id=document_id,
            chunk_id=chunk.id,
            excerpt=chunk.text,
            locator={
                "block_id": chunk.block_id,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
            },
        )
        return RetrievedItem(
            chunk_id=chunk.id,
            document_id=document_id,
            source_tier=source_tier,
            chunk_type=chunk.chunk_type,
            text=chunk.text,
            score=score,
            citation=citation,
            backend=backend,
            backend_scores={backend: score},
            embedding_model_version=model_version,
            retrieved_at=now,
        )

    def _empty_trace(
        self,
        request: RetrievalRequest,
        model_version: str,
        filters: dict[str, Any],
        now: datetime,
    ) -> RetrievalTrace:
        return RetrievalTrace(
            request=request,
            embedding_model_version=model_version,
            filters=filters,
            candidate_count=0,
            items=[],
            generated_at=now,
        )
