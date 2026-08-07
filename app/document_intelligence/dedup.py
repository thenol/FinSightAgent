"""跨渠道去重：把同一披露的不同载体归入 DisclosureGroup。"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

from app.domain import DisclosureGroup, DisclosureGroupMembership, Document, DocumentChunk
from app.platform.ids import new_id
from app.platform.repository import Repository


class _EmbeddingServiceProtocol(Protocol):
    """DisclosureGroupService 仅依赖的 EmbeddingService 最小接口。"""

    def representative_embedding(
        self, chunks: list[DocumentChunk], model_version: Optional[str] = None
    ) -> Optional[list[float]]: ...

    @property
    def provider(self): ...


def _normalize_text(text: str) -> str:
    """语义归一化：压缩空白、统一标点、小写化非 CJK。"""
    text = re.sub(r"[ \t\r\n]+", " ", text)
    text = text.replace("，", ",").replace("。", ".").replace("；", ";")
    text = text.replace("：", ":").replace("？", "?").replace("！", "!")
    text = text.strip()
    return text


def _canonical_content_hash(title: str, chunks: list[DocumentChunk]) -> str:
    """基于标题和 chunk 内容指纹生成稳定哈希。

    同一正文不同顺序的 chunk 通过排序获得相同哈希；
    不同正文仅顺序调换的情况极少，可接受。
    """
    normalized_title = _normalize_text(title)
    chunk_hashes = sorted({chunk.content_hash for chunk in chunks})
    canonical = "\n".join([normalized_title, *chunk_hashes])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class SemanticDedupConfig:
    """语义去重可调参数。"""

    enabled: bool = True
    similarity_threshold: float = 0.92
    max_candidates: int = 200


class DisclosureGroupService:
    """管理 DisclosureGroup 生命周期与文档加入策略。"""

    def __init__(
        self,
        repository: Repository,
        embedding_service: Optional[_EmbeddingServiceProtocol] = None,
        semantic_config: Optional[SemanticDedupConfig] = None,
    ) -> None:
        self.repository = repository
        self.embedding_service = embedding_service
        self.semantic_config = semantic_config or SemanticDedupConfig()

    def find_or_create_group(
        self, document: Document, chunks: list[DocumentChunk]
    ) -> tuple[DisclosureGroup, DisclosureGroupMembership, bool]:
        """返回 (group, membership, created)。

        1. 先按精确 canonical content hash 匹配；
        2. 未命中时，若配置了 EmbeddingService，则按语义向量相似度匹配；
        3. 仍无匹配则新建 DisclosureGroup。
        """
        canonical_hash = _canonical_content_hash(document.title, chunks)
        existing = self.repository.find_disclosure_group_by_content_hash(canonical_hash)
        if existing is not None:
            membership = self._add_membership(existing, document, reason="exact_hash")
            return existing, membership, False

        if self.semantic_config.enabled and self.embedding_service is not None:
            semantic_match = self._find_semantic_match(document, chunks)
            if semantic_match is not None:
                membership = self._add_membership(
                    semantic_match, document, reason="semantic_similarity"
                )
                self._maybe_update_group_representative(semantic_match, chunks)
                return semantic_match, membership, False

        now = datetime.now(timezone.utc)
        group = DisclosureGroup(
            id=new_id("dsg"),
            canonical_content_hash=canonical_hash,
            canonical_document_id=document.id,
            entity_ids=[],
            event_type_hints=[],
            representative_embedding=self._compute_representative_embedding(chunks),
            embedding_model_version=self._embedding_model_version(),
            created_at=now,
            updated_at=now,
        )
        self.repository.save_disclosure_group(group)
        membership = self._add_membership(group, document, reason="exact_hash")
        return group, membership, True

    def _find_semantic_match(
        self, document: Document, chunks: list[DocumentChunk]
    ) -> Optional[DisclosureGroup]:
        document_embedding = self.embedding_service.representative_embedding(chunks)
        if document_embedding is None:
            return None

        model_version = self._embedding_model_version()
        if model_version is None:
            return None

        # PostgreSQL 路径使用 pgvector 向量相似度召回；SQLite/内存路径回退到 brute-force。
        candidates = self.repository.find_similar_disclosure_groups(
            document_embedding,
            model_version,
            top_k=self.semantic_config.max_candidates,
        )

        best_group: Optional[DisclosureGroup] = None
        best_score = 0.0
        for group, score in candidates:
            if score > best_score:
                best_score = score
                best_group = group

        if best_group is not None and best_score >= self.semantic_config.similarity_threshold:
            return best_group
        return None

    def _maybe_update_group_representative(
        self, group: DisclosureGroup, chunks: list[DocumentChunk]
    ) -> None:
        """新文档加入后，用已有成员与新文档 embeddings 的均值更新组代表向量。"""
        new_embedding = self.embedding_service.representative_embedding(chunks)
        if new_embedding is None:
            return
        current = group.representative_embedding
        if current is None:
            updated = new_embedding
        else:
            updated = self._mean_vectors([current, new_embedding])
        # frozen dataclass 需要用 replace 更新。
        from dataclasses import replace

        self.repository.save_disclosure_group(replace(group, representative_embedding=updated))

    def _compute_representative_embedding(
        self, chunks: list[DocumentChunk]
    ) -> Optional[list[float]]:
        if self.embedding_service is None:
            return None
        return self.embedding_service.representative_embedding(chunks)

    def _embedding_model_version(self) -> Optional[str]:
        if self.embedding_service is None:
            return None
        return self.embedding_service.provider.model_version

    @staticmethod
    def _mean_vectors(vectors: list[list[float]]) -> list[float]:
        from app.document_intelligence.embeddings import mean_vector

        return mean_vector(vectors)

    def _add_membership(
        self, group: DisclosureGroup, document: Document, reason: str
    ) -> DisclosureGroupMembership:
        # 幂等：同一文档已加入则直接返回。
        members = self.repository.list_disclosure_group_members(group.id)
        for member in members:
            if member.document_id == document.id:
                return member

        membership = DisclosureGroupMembership(
            id=new_id("dgm"),
            disclosure_group_id=group.id,
            document_id=document.id,
            source_tier=document.source_tier,
            reason=reason,
            joined_at=datetime.now(timezone.utc),
        )
        self.repository.save_disclosure_group_membership(membership)
        return membership

    def get_group_for_document(self, document_id: str) -> Optional[DisclosureGroup]:
        return self.repository.get_disclosure_group_for_document(document_id)
