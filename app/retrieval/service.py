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
from app.retrieval.planner import QueryPlanner


class RetrievalService:
    """提供基于 Embedding、关键词或混合模式的 DocumentChunk 检索。"""

    def __init__(
        self,
        repository: Repository,
        embedding_service: Optional[EmbeddingService] = None,
        fusion_service: Optional[FusionService] = None,
        planner: Optional[QueryPlanner] = None,
    ) -> None:
        self.repository = repository
        self.embedding_service = embedding_service or EmbeddingService(repository)
        self.fusion_service = fusion_service or FusionService()
        self.planner = planner or QueryPlanner(repository)

    def retrieve(self, request: RetrievalRequest) -> RetrievalTrace:
        """执行检索并返回带审计轨迹的结果。"""
        if request.retrieval_mode == "lexical":
            return self._retrieve_lexical(request)
        if request.retrieval_mode == "hybrid":
            return self._retrieve_hybrid(request)
        if request.retrieval_mode == "graph":
            return self._retrieve_graph(request)
        if request.retrieval_mode == "sql":
            return self._retrieve_structured(request)
        if request.retrieval_mode == "timeseries":
            return self._retrieve_timeseries(request)
        if request.retrieval_mode == "planned":
            return self._retrieve_planned(request)
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

    def _retrieve_graph(self, request: RetrievalRequest) -> RetrievalTrace:
        """Graph-like 检索：基于 Event/Entity/Claim 关系做关联召回。"""
        now = datetime.now(timezone.utc)
        plan = self.planner.plan(request.query, top_k=request.top_k, as_of=request.as_of)
        intent = plan.intents[0] if plan.intents else None
        entity_ids = intent.entity_ids if intent else []
        start, end = intent.time_range if intent else (None, None)
        filters: dict[str, Any] = {
            "as_of": request.as_of.isoformat() if request.as_of else None,
            "entity_ids": entity_ids,
            "time_range": [
                start.isoformat() if start else None,
                end.isoformat() if end else None,
            ],
        }

        events = self._events_for_graph(entity_ids, start, end, request.as_of)
        items: list[RetrievedItem] = []
        seen_chunks: set[str] = set()
        for event, hop in events:
            score = max(0.5, 1.0 - (hop - 1) * 0.2)
            for chunk in self._chunks_for_event(event, request.chunk_types, request.source_tiers):
                if chunk.id in seen_chunks:
                    continue
                seen_chunks.add(chunk.id)
                items.append(self._build_item(chunk, score, "", now, backend="graph"))
            if len(items) >= request.top_k:
                break

        return RetrievalTrace(
            request=request,
            embedding_model_version="",
            filters=filters,
            candidate_count=len(events),
            items=items[: request.top_k],
            generated_at=now,
        )

    def _retrieve_structured(self, request: RetrievalRequest) -> RetrievalTrace:
        """Structured SQL 检索：按事件类型、时间、重要度过滤。"""
        now = datetime.now(timezone.utc)
        plan = self.planner.plan(request.query, top_k=request.top_k, as_of=request.as_of)
        intent = plan.intents[0] if plan.intents else None
        event_types = intent.event_types if intent else []
        start, end = intent.time_range if intent else (None, None)

        filters: dict[str, Any] = {
            "as_of": request.as_of.isoformat() if request.as_of else None,
            "event_types": event_types,
            "time_range": [
                start.isoformat() if start else None,
                end.isoformat() if end else None,
            ],
        }

        events = self.repository.list_events(as_of=request.as_of, limit=10_000)
        items: list[RetrievedItem] = []
        for event in events:
            if event_types and event.event_type not in event_types:
                continue
            if not self._in_time_range(event.occurred_at, start, end):
                continue
            items.append(self._build_item_from_event(event, 1.0, now, backend="sql"))
            if len(items) >= request.top_k:
                break

        return RetrievalTrace(
            request=request,
            embedding_model_version="",
            filters=filters,
            candidate_count=len(items),
            items=items,
            generated_at=now,
        )

    def _retrieve_timeseries(self, request: RetrievalRequest) -> RetrievalTrace:
        """Time-series 检索：按时间窗倒序列出事件及相关文档块。"""
        now = datetime.now(timezone.utc)
        plan = self.planner.plan(request.query, top_k=request.top_k, as_of=request.as_of)
        intent = plan.intents[0] if plan.intents else None
        start, end = intent.time_range if intent else (None, None)

        filters: dict[str, Any] = {
            "as_of": request.as_of.isoformat() if request.as_of else None,
            "time_range": [
                start.isoformat() if start else None,
                end.isoformat() if end else None,
            ],
        }

        events = self.repository.list_events(as_of=request.as_of, limit=10_000)
        events = [e for e in events if self._in_time_range(e.occurred_at, start, end)]
        events.sort(key=lambda e: e.occurred_at, reverse=True)

        items: list[RetrievedItem] = []
        seen_chunks: set[str] = set()
        for event in events:
            for chunk in self._chunks_for_event(event, request.chunk_types, request.source_tiers):
                if chunk.id in seen_chunks:
                    continue
                seen_chunks.add(chunk.id)
                items.append(self._build_item(chunk, 1.0, "", now, backend="timeseries"))
            if len(items) >= request.top_k:
                break

        return RetrievalTrace(
            request=request,
            embedding_model_version="",
            filters=filters,
            candidate_count=len(events),
            items=items[: request.top_k],
            generated_at=now,
        )

    def _retrieve_planned(self, request: RetrievalRequest) -> RetrievalTrace:
        """执行 Planner 生成的多后端计划并融合结果。"""
        now = datetime.now(timezone.utc)
        plan = self.planner.plan(request.query, top_k=request.top_k, as_of=request.as_of)
        per_backend_top_k = max(request.top_k * 3, 10)

        backend_items: dict[str, list[RetrievedItem]] = {}
        for backend in plan.backends:
            sub_request = RetrievalRequest(
                query=request.query,
                embedding_model_version=request.embedding_model_version,
                top_k=per_backend_top_k,
                as_of=request.as_of,
                chunk_types=request.chunk_types,
                source_tiers=request.source_tiers,
                min_score=0.0,
                retrieval_mode=backend if backend != "hybrid" else "hybrid",
            )
            trace = self.retrieve(sub_request)
            backend_items[backend] = trace.items

        fused_items, fusion_trace = self.fusion_service.fuse(
            backend_items,
            FusionConfig(method="rrf", max_items=request.top_k),
        )

        if request.min_score > 0:
            fused_items = [item for item in fused_items if item.score >= request.min_score]

        filters: dict[str, Any] = {
            "as_of": request.as_of.isoformat() if request.as_of else None,
            "plan": {
                "intents": [
                    {
                        "intent": i.intent,
                        "entity_ids": i.entity_ids,
                        "event_types": i.event_types,
                        "time_range": [
                            i.time_range[0].isoformat() if i.time_range[0] else None,
                            i.time_range[1].isoformat() if i.time_range[1] else None,
                        ],
                    }
                    for i in plan.intents
                ],
                "backends": plan.backends,
                "primary_backend": plan.primary_backend,
            },
            "fusion": fusion_trace,
        }

        return RetrievalTrace(
            request=request,
            embedding_model_version=request.embedding_model_version or "",
            filters=filters,
            candidate_count=sum(len(v) for v in backend_items.values()),
            items=fused_items,
            fusion_method=fusion_trace["method"],
            backend_coverage=fusion_trace["backend_coverage"],
            generated_at=now,
        )

    def _events_for_graph(
        self,
        entity_ids: list[str],
        start: Optional[datetime],
        end: Optional[datetime],
        as_of: Optional[datetime],
    ) -> list[tuple[Any, int]]:
        """返回 (event, hop) 列表。hop=1 表示实体直接关联事件。"""
        entity_set = set(entity_ids)
        events = self.repository.list_events(as_of=as_of, limit=10_000)
        matches: list[tuple[Any, int]] = []
        for event in events:
            if not self._in_time_range(event.occurred_at, start, end):
                continue
            if entity_set and any(eid in entity_set for eid in event.entity_ids):
                matches.append((event, 1))
        # 按 hop 与重要度排序
        matches.sort(key=lambda x: (x[1], -x[0].importance))
        return matches

    def _chunks_for_event(
        self,
        event: Any,
        chunk_types: Optional[list[str]],
        source_tiers: Optional[list[str]],
    ) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        for document_id in event.document_ids:
            document = self.repository.get_document(document_id)
            if document is None:
                continue
            if source_tiers and document.source_tier not in source_tiers:
                continue
            revision = self.repository.get_latest_revision(document_id)
            if revision is None:
                continue
            blocks = self.repository.get_document_blocks_for_revision(revision.id)
            for block in blocks:
                for chunk in self.repository.get_document_chunks_for_block(block.id):
                    if chunk_types and chunk.chunk_type not in chunk_types:
                        continue
                    chunks.append(chunk)
        return chunks

    def _build_item_from_event(
        self,
        event: Any,
        score: float,
        now: datetime,
        backend: str = "sql",
    ) -> RetrievedItem:
        document_id = event.document_ids[0] if event.document_ids else ""
        citation = CitationCandidate(
            document_id=document_id,
            chunk_id=event.id,
            excerpt=event.title,
            locator={"event_id": event.id, "event_type": event.event_type},
        )
        return RetrievedItem(
            chunk_id=event.id,
            document_id=document_id,
            source_tier="",
            chunk_type="event_summary",
            text=event.title,
            score=score,
            citation=citation,
            backend=backend,
            backend_scores={backend: score},
            retrieved_at=now,
        )

    @staticmethod
    def _in_time_range(
        dt: Optional[datetime],
        start: Optional[datetime],
        end: Optional[datetime],
    ) -> bool:
        if dt is None:
            return False
        if start is not None and dt < start:
            return False
        if end is not None and dt > end:
            return False
        return True

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
