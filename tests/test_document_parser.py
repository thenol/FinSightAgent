from datetime import datetime, timezone

from app.document_intelligence.parser import DocumentParser
from app.domain import Document, DocumentRevision


def _document(content: str) -> tuple[Document, DocumentRevision]:
    document = Document(
        id="doc_001",
        source_id="src",
        source_tier="S",
        external_id="ext-001",
        canonical_url="https://example.test/1",
        title="示例公告",
        content=content,
        content_hash="hash",
        published_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )
    revision = DocumentRevision(
        id="rev_001",
        document_id=document.id,
        revision_no=1,
        artifact_id="art_001",
        content_hash="hash",
        normalized_content_uri="uri",
        parser_version="inline-v1",
        created_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )
    return document, revision


def test_parser_produces_parsed_document_and_blocks() -> None:
    document, revision = _document("公司预计净利润增长。\n合同金额为1亿元。")
    parsed, blocks = DocumentParser().parse(document, revision)

    assert parsed.document_id == document.id
    assert parsed.revision_id == revision.id
    assert parsed.block_ids == [block.id for block in blocks]
    assert len(blocks) == 2
    assert blocks[0].block_id == "body-p-001"
    assert blocks[1].block_id == "body-p-002"


def test_block_offsets_point_to_original_text() -> None:
    document, revision = _document("首段内容。\n第二段内容。")
    parsed, blocks = DocumentParser().parse(document, revision)

    for block in blocks:
        assert document.content[block.char_start:block.char_end] == block.text


def test_summary_comes_from_first_blocks() -> None:
    document, revision = _document("第一段。\n第二段。\n第三段。\n第四段。")
    parsed, _ = DocumentParser().parse(document, revision)

    assert parsed.summary is not None
    assert "第一段" in parsed.summary
    assert "第四段" not in parsed.summary
