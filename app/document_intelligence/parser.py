"""文档解析：把 Document/Revision 转换为 ParsedDocument + DocumentBlock[]。

当前实现复用 ``DocumentBlockReader`` 生成与原文逐字对齐的段落块；
后续接入真实 HTML/PDF 解析器时，只需新增解析分支并升级 parser_version，
已落库 Evidence 仍可通过 revision_id + extraction_version 回放。
"""

from datetime import datetime, timezone
from typing import Optional

from app.domain import Document, DocumentBlock, DocumentRevision, ParsedDocument
from app.ingestion.blocks import PARSER_VERSION, DocumentBlockReader
from app.platform.ids import new_id


def _detect_language(title: str) -> str:
    # 简单启发：含 CJK 字符即视为中文。
    if any("\u4e00" <= ch <= "\u9fff" for ch in title):
        return "zh"
    return "en"


class DocumentParser:
    """按段落把正文解析为稳定 DocumentBlock，并生成 ParsedDocument。"""

    def __init__(self, block_reader: Optional[DocumentBlockReader] = None) -> None:
        self.block_reader = block_reader or DocumentBlockReader()

    def parse(
        self, document: Document, revision: DocumentRevision
    ) -> tuple[ParsedDocument, list[DocumentBlock]]:
        raw_blocks = self.block_reader.parse(document.content)
        parsed_id = new_id("pdoc")
        parser_run_id = new_id("prr")
        now = datetime.now(timezone.utc)

        blocks: list[DocumentBlock] = []
        for order_index, raw in enumerate(raw_blocks, start=1):
            blocks.append(
                DocumentBlock(
                    id=new_id("blk"),
                    parsed_document_id=parsed_id,
                    revision_id=revision.id,
                    block_type="paragraph",
                    block_id=raw.block_id,
                    text=raw.text,
                    char_start=raw.char_start,
                    char_end=raw.char_end,
                    order_index=order_index,
                    dom_path=raw.dom_path,
                    page_no=raw.page,
                    metadata={},
                    created_at=now,
                )
            )

        summary = "\n".join(block.text for block in raw_blocks[:3])
        parsed = ParsedDocument(
            id=parsed_id,
            document_id=document.id,
            revision_id=revision.id,
            parser_version=f"doc-intel-{PARSER_VERSION}",
            parser_run_id=parser_run_id,
            language=_detect_language(document.title),
            title=document.title,
            block_ids=[block.id for block in blocks],
            summary=summary if summary else None,
            created_at=now,
        )
        return parsed, blocks
