"""语义 Chunking：把 DocumentBlock 切分为带金融语义类型的检索单元。"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

from app.domain import DocumentBlock, DocumentChunk
from app.platform.ids import new_id

CHUNK_TYPES = (
    "event_description",
    "financial_impact",
    "risk",
    "footnote",
    "background",
)

_FINANCIAL_RE = re.compile(
    r"(?:净利润|营业收入|营收|利润|金额|合同|交易对价|减持|增持|罚款|处罚|同比增长|同比下降|同比|环比|万元|亿元|元|%|百分比)",
    re.UNICODE,
)
_RISK_RE = re.compile(r"(?:风险|不确定性|波动|可能|或存在|敬请注意)", re.UNICODE)
_FOOTNOTE_RE = re.compile(r"(?:来源|注释|注：|注:|资料来源|免责声明)", re.UNICODE)

_SENTENCE_DELIMITERS = re.compile(r"([。！？；])", re.UNICODE)


def _chunk_type(text: str, is_first_block: bool) -> str:
    if is_first_block:
        return "event_description"
    if _FOOTNOTE_RE.search(text):
        return "footnote"
    if _RISK_RE.search(text):
        return "risk"
    if _FINANCIAL_RE.search(text):
        return "financial_impact"
    return "background"


def _split_sentences(text: str) -> list[tuple[str, int, int]]:
    """返回 (句子, 起始偏移, 结束偏移) 列表。"""
    parts = _SENTENCE_DELIMITERS.split(text)
    chunks: list[tuple[str, int, int]] = []
    offset = 0
    buffer = ""
    for part in parts:
        buffer += part
        if part in "。！？；":
            stripped = buffer.strip()
            if stripped:
                start = offset + (len(buffer) - len(buffer.lstrip()))
                end = start + len(stripped)
                chunks.append((stripped, start, end))
            offset += len(buffer)
            buffer = ""
    if buffer.strip():
        start = offset + (len(buffer) - len(buffer.lstrip()))
        stripped = buffer.strip()
        end = start + len(stripped)
        chunks.append((stripped, start, end))
    return chunks


class SemanticChunker:
    """基于规则把 Block 切分为语义 Chunk，保持原文可回溯。"""

    def __init__(self, max_chunk_chars: int = 512) -> None:
        self.max_chunk_chars = max_chunk_chars

    @staticmethod
    def _flush_parts(
        parts: list[tuple[str, int, int]],
        block: DocumentBlock,
        chunk_type: str,
        as_of: datetime,
    ) -> Optional[DocumentChunk]:
        if not parts:
            return None
        text = "".join(part for part, _, _ in parts).strip()
        if not text:
            parts.clear()
            return None
        start_part = parts[0]
        start = block.char_start + start_part[1]
        end = start + len(text)
        chunk = DocumentChunk(
            id=new_id("chk"),
            block_id=block.id,
            chunk_type=chunk_type,
            text=text,
            char_start=start,
            char_end=end,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            as_of=as_of,
            created_at=as_of,
        )
        parts.clear()
        return chunk

    def chunk(
        self, blocks: list[DocumentBlock], as_of: Optional[datetime] = None
    ) -> list[DocumentChunk]:
        now = datetime.now(timezone.utc)
        as_of = as_of or now
        chunks: list[DocumentChunk] = []
        for index, block in enumerate(blocks):
            chunk_type = _chunk_type(block.text, index == 0)
            sentences = _split_sentences(block.text)
            current_parts: list[tuple[str, int, int]] = []
            current_len = 0

            for sentence, s_start, s_end in sentences:
                if current_len + len(sentence) > self.max_chunk_chars and current_parts:
                    flushed = self._flush_parts(current_parts, block, chunk_type, as_of)
                    if flushed is not None:
                        chunks.append(flushed)
                    current_len = 0
                current_parts.append((sentence, s_start, s_end))
                current_len += len(sentence)

            flushed = self._flush_parts(current_parts, block, chunk_type, as_of)
            if flushed is not None:
                chunks.append(flushed)
        return chunks
