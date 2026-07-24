from pathlib import Path

import fitz
import pytest

from app.ingestion.ocr import (
    DETERMINISTIC_MODEL,
    DETERMINISTIC_PROVIDER,
    DETERMINISTIC_VERSION,
    DeterministicOcrFixture,
    DeterministicOcrProvider,
    NormalizedBBox,
    OcrErrorCode,
    OcrRequest,
    OcrSuccess,
    OcrTextBlock,
)
from app.ingestion.pdf import PARSER_VERSION, PdfBlockParser, PdfOcrPolicy, PdfTextUnavailable

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "scanned-financial-disclosure.pdf"


def make_text_pdf(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 40), text)
    content = document.tobytes()
    document.close()
    return content


def scanned_provider(content: bytes) -> DeterministicOcrProvider:
    request = OcrRequest(content=content)
    return DeterministicOcrProvider(
        [
            DeterministicOcrFixture(
                document_sha256=request.document_sha256,
                page_count=1,
                blocks=(
                    OcrTextBlock(
                        page_number=1,
                        bbox=NormalizedBBox(0.1, 0.1, 0.9, 0.2),
                        text="Revenue increased 12 percent.",
                        confidence=0.98,
                    ),
                    OcrTextBlock(
                        page_number=1,
                        bbox=NormalizedBBox(0.1, 0.3, 0.8, 0.4),
                        text="Net income was 42 million.",
                        confidence=0.95,
                    ),
                ),
            )
        ]
    )


def test_text_layer_is_preferred_when_sufficient() -> None:
    provider = DeterministicOcrProvider(failure_code=OcrErrorCode.PROVIDER_FAILURE)
    result = PdfBlockParser(provider).parse(make_text_pdf("Financial disclosure"))

    assert result.extraction_method == "text-layer"
    assert result.text == "Financial disclosure"
    assert result.ocr_provider is None
    assert result.extraction_version == PARSER_VERSION


def test_scanned_pdf_uses_structured_deterministic_ocr_stub() -> None:
    content = FIXTURE_PATH.read_bytes()
    provider = scanned_provider(content)

    provider_result = provider.recognize(OcrRequest(content=content))
    assert isinstance(provider_result, OcrSuccess)
    assert provider_result.blocks[0].page_number == 1
    assert provider_result.blocks[0].bbox.as_tuple() == (0.1, 0.1, 0.9, 0.2)
    assert provider_result.blocks[0].confidence == 0.98
    assert (provider_result.provider, provider_result.model, provider_result.version) == (
        DETERMINISTIC_PROVIDER,
        DETERMINISTIC_MODEL,
        DETERMINISTIC_VERSION,
    )

    result = PdfBlockParser(provider).parse(content)
    assert result.extraction_method == "ocr"
    assert result.text == (
        "Revenue increased 12 percent.\n"
        "Net income was 42 million."
    )
    assert result.page_count == 1
    assert all(block.page == 1 for block in result.blocks)
    assert all(
        block.bbox is not None and all(0.0 <= coordinate <= 1.0 for coordinate in block.bbox)
        for block in result.blocks
    )


def test_ocr_failure_degrades_to_insufficient_nonempty_text_layer() -> None:
    content = make_text_pdf("short")
    provider = DeterministicOcrProvider(failure_code=OcrErrorCode.PROVIDER_FAILURE)

    result = PdfBlockParser(
        provider,
        ocr_policy=PdfOcrPolicy(minimum_text_characters=20),
    ).parse(content)

    assert result.text == "short"
    assert result.extraction_method == "text-layer"
    assert result.ocr_error_code == OcrErrorCode.PROVIDER_FAILURE.value


def test_ocr_failure_with_no_text_is_not_reported_as_success() -> None:
    content = FIXTURE_PATH.read_bytes()
    provider = DeterministicOcrProvider(failure_code=OcrErrorCode.PROVIDER_FAILURE)

    with pytest.raises(PdfTextUnavailable, match=OcrErrorCode.PROVIDER_FAILURE.value):
        PdfBlockParser(provider).parse(content)


def test_ocr_locators_offsets_and_versions_are_stable() -> None:
    content = FIXTURE_PATH.read_bytes()
    parser = PdfBlockParser(scanned_provider(content))

    first = parser.parse(content)
    second = parser.parse(content)

    assert first.blocks == second.blocks
    assert first.extraction_version == second.extraction_version
    assert first.extraction_version == (
        f"{PARSER_VERSION}+"
        f"{DETERMINISTIC_PROVIDER}/{DETERMINISTIC_MODEL}/{DETERMINISTIC_VERSION}"
    )
    assert [block.block_id for block in first.blocks] == ["p001-ocr001", "p001-ocr002"]
    for block in first.blocks:
        assert first.text[block.char_start : block.char_end] == block.text
