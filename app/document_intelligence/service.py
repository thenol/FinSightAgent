"""Document Intelligence 应用服务：编排解析、分块、Embedding、去重。"""

from app.document_intelligence.chunker import SemanticChunker
from app.document_intelligence.dedup import DisclosureGroupService
from app.document_intelligence.embeddings import EmbeddingService
from app.document_intelligence.parser import DocumentParser
from app.domain import (
    DisclosureGroup,
    Document,
    DocumentBlock,
    DocumentChunk,
    ParsedDocument,
)
from app.platform.repository import Repository


class DocumentIntelligenceService:
    """把 Document 解析为 Block/Chunk，生成 Embedding，并归入 DisclosureGroup。"""

    def __init__(
        self,
        repository: Repository,
        parser: DocumentParser | None = None,
        chunker: SemanticChunker | None = None,
        embedding_service: EmbeddingService | None = None,
        dedup: DisclosureGroupService | None = None,
    ) -> None:
        self.repository = repository
        self.parser = parser or DocumentParser()
        self.chunker = chunker or SemanticChunker()
        self.embedding_service = embedding_service or EmbeddingService(repository)
        self.dedup = dedup or DisclosureGroupService(
            repository, embedding_service=self.embedding_service
        )

    def process(
        self, document: Document
    ) -> tuple[ParsedDocument, list[DocumentBlock], list[DocumentChunk], DisclosureGroup]:
        revision = self.repository.get_latest_revision(document.id)
        if revision is None:
            raise RuntimeError("DOCUMENT_REVISION_MISSING")

        parsed, blocks = self.parser.parse(document, revision)
        chunks = self.chunker.chunk(blocks, as_of=document.published_at)

        self.repository.save_parsed_document(parsed)
        for block in blocks:
            self.repository.save_document_block(block)
        for chunk in chunks:
            self.repository.save_document_chunk(chunk)

        # 生成 Embedding 生命周期记录；去重服务可选择使用语义向量。
        self.embedding_service.embed_chunks(chunks)

        group, _, _ = self.dedup.find_or_create_group(document, chunks)
        return parsed, blocks, chunks, group
