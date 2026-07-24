import fitz
import pytest

from app.ingestion.pdf import PdfBlockParser, PdfTextUnavailable


def make_text_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 40), "Financial disclosure")
    content = document.tobytes()
    document.close()
    return content


def test_pdf_parser_returns_page_and_normalized_bbox() -> None:
    result = PdfBlockParser().parse(make_text_pdf())

    assert result.page_count == 1
    assert result.blocks[0].locator_type == "pdf"
    assert result.blocks[0].page == 1
    assert result.blocks[0].text == "Financial disclosure"
    assert result.blocks[0].bbox is not None
    assert all(0 <= coordinate <= 1 for coordinate in result.blocks[0].bbox)


def test_pdf_without_text_requires_ocr() -> None:
    document = fitz.open()
    document.new_page()
    content = document.tobytes()
    document.close()

    with pytest.raises(PdfTextUnavailable, match="OCR_REQUIRED"):
        PdfBlockParser().parse(content)
