from app.document_intelligence.chunker import SemanticChunker
from app.domain import DocumentBlock


def _block(text: str, start: int = 0) -> DocumentBlock:
    return DocumentBlock(
        id="blk_001",
        parsed_document_id="pdoc_001",
        revision_id="rev_001",
        block_type="paragraph",
        block_id="body-p-001",
        text=text,
        char_start=start,
        char_end=start + len(text),
        order_index=1,
    )


def test_first_block_is_event_description() -> None:
    block = _block("公司发布业绩预告。")
    chunks = SemanticChunker().chunk([block])

    assert len(chunks) == 1
    assert chunks[0].chunk_type == "event_description"


def test_financial_block_is_financial_impact() -> None:
    blocks = [
        _block("公司发布业绩预告。"),
        _block("公司预计净利润同比增长20%至30%。"),
    ]
    chunks = SemanticChunker().chunk(blocks)

    financial = [c for c in chunks if c.chunk_type == "financial_impact"]
    assert financial
    assert "净利润" in financial[0].text


def test_risk_block_is_risk() -> None:
    blocks = [
        _block("公司发布交易公告。"),
        _block("本次交易存在不确定性风险。"),
    ]
    chunks = SemanticChunker().chunk(blocks)

    risk = [c for c in chunks if c.chunk_type == "risk"]
    assert risk
    assert "风险" in risk[0].text


def test_chunk_offsets_are_absolute() -> None:
    text = "公司预计净利润增长。"
    block = _block(text, start=10)
    chunks = SemanticChunker().chunk([block])

    assert chunks[0].char_start == 10
    assert chunks[0].char_end == 10 + len(text)


def test_long_block_splits_by_sentence() -> None:
    text = "第一句。第二句。第三句。"
    block = _block(text)
    chunks = SemanticChunker(max_chunk_chars=10).chunk([block])

    assert len(chunks) >= 2
    combined = "".join(c.text for c in chunks)
    assert combined == text
