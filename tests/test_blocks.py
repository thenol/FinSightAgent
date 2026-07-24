from app.ingestion.blocks import PARSER_VERSION, DocumentBlockReader


def test_single_line_content_produces_one_block_with_exact_offset() -> None:
    content = "公司预计2026年半年度净利润同比增长20%至30%。"
    blocks = DocumentBlockReader().parse(content)

    assert len(blocks) == 1
    block = blocks[0]
    assert block.block_id == "body-p-001"
    assert block.locator_type == "html"
    assert block.char_start == 0
    assert block.char_end == len(content)
    assert block.text == content
    assert content[block.char_start : block.char_end] == block.text


def test_multiple_paragraphs_get_sequential_block_ids_and_precise_offsets() -> None:
    content = "第一段内容。\n\n第二段内容更长一些。\n第三段。"
    blocks = DocumentBlockReader().parse(content)

    assert [block.block_id for block in blocks] == ["body-p-001", "body-p-002", "body-p-003"]
    for block in blocks:
        assert content[block.char_start : block.char_end] == block.text
    assert blocks[0].text == "第一段内容。"
    assert blocks[1].text == "第二段内容更长一些。"
    assert blocks[2].text == "第三段。"


def test_blank_lines_do_not_produce_blocks() -> None:
    assert DocumentBlockReader().parse("\n\n  \n\n") == []


def test_empty_content_produces_no_blocks() -> None:
    assert DocumentBlockReader().parse("") == []


def test_leading_and_trailing_whitespace_is_excluded_from_offset() -> None:
    content = "   有缩进的段落。   \n"
    blocks = DocumentBlockReader().parse(content)

    assert len(blocks) == 1
    block = blocks[0]
    assert block.text == "有缩进的段落。"
    assert content[block.char_start : block.char_end] == "有缩进的段落。"


def test_parser_version_is_versioned() -> None:
    assert PARSER_VERSION == "html-blocks-v1"
