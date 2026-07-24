"""Replayable PDF text-layer parsing with a versioned OCR fallback."""

from __future__ import annotations

from dataclasses import dataclass

import fitz

from app.ingestion.blocks import DocumentBlock
from app.ingestion.ocr import (
    OcrErrorCode,
    OcrFailure,
    OcrProvider,
    OcrRequest,
    OcrSuccess,
)

PARSER_VERSION = "pdf-pymupdf-v2"


class PdfTextUnavailable(ValueError):
    """PDF produced no usable text; the message is a stable machine error code."""


@dataclass(frozen=True)
class PdfOcrPolicy:
    """Policy controlling when text-layer extraction falls back to OCR."""

    minimum_text_characters: int = 1

    def __post_init__(self) -> None:
        if self.minimum_text_characters < 1:
            raise ValueError("PDF_OCR_THRESHOLD_INVALID")


@dataclass(frozen=True)
class PdfParseResult:
    blocks: list[DocumentBlock]
    text: str
    page_count: int
    parser_version: str = PARSER_VERSION
    extraction_method: str = "text-layer"
    ocr_provider: str | None = None
    ocr_model: str | None = None
    ocr_version: str | None = None
    ocr_error_code: str | None = None

    @property
    def extraction_version(self) -> str:
        """Version identity sufficient to replay the selected extraction path."""

        if self.extraction_method == "ocr":
            return (
                f"{self.parser_version}+"
                f"{self.ocr_provider}/{self.ocr_model}/{self.ocr_version}"
            )
        return self.parser_version


@dataclass(frozen=True)
class _PdfCandidate:
    page_number: int
    bbox: tuple[float, float, float, float]
    text: str


class PdfBlockParser:
    def __init__(
        self,
        ocr_provider: OcrProvider | None = None,
        *,
        ocr_policy: PdfOcrPolicy | None = None,
    ) -> None:
        self.ocr_provider = ocr_provider
        self.ocr_policy = ocr_policy or PdfOcrPolicy()

    def parse(self, content: bytes) -> PdfParseResult:
        try:
            document = fitz.open(stream=content, filetype="pdf")
        except (fitz.FileDataError, RuntimeError) as exc:
            raise ValueError("PDF_PARSE_FAILED") from exc

        try:
            page_count = document.page_count
            candidates = [
                candidate
                for page_number, page in enumerate(document, start=1)
                for candidate in self._page_candidates(page, page_number)
            ]
        finally:
            document.close()

        text_layer = self._result_from_candidates(candidates, page_count)
        if len(text_layer.text.strip()) >= self.ocr_policy.minimum_text_characters:
            return text_layer
        if self.ocr_provider is None:
            if text_layer.text:
                return PdfParseResult(
                    blocks=text_layer.blocks,
                    text=text_layer.text,
                    page_count=page_count,
                    ocr_error_code="OCR_PROVIDER_NOT_CONFIGURED",
                )
            raise PdfTextUnavailable("OCR_REQUIRED")
        return self._ocr_or_degrade(content, text_layer, page_count)

    def _page_candidates(self, page: fitz.Page, page_number: int) -> list[_PdfCandidate]:
        page_width = max(page.rect.width, 1.0)
        page_height = max(page.rect.height, 1.0)
        candidates: list[_PdfCandidate] = []
        for raw in page.get_text("blocks"):
            x0, y0, x1, y1, value, *_ = raw
            text = " ".join(value.split())
            if text:
                candidates.append(
                    _PdfCandidate(
                        page_number=page_number,
                        bbox=(
                            round(max(0.0, min(1.0, x0 / page_width)), 6),
                            round(max(0.0, min(1.0, y0 / page_height)), 6),
                            round(max(0.0, min(1.0, x1 / page_width)), 6),
                            round(max(0.0, min(1.0, y1 / page_height)), 6),
                        ),
                        text=text,
                    )
                )
        return candidates

    def _result_from_candidates(
        self,
        candidates: list[_PdfCandidate],
        page_count: int,
        *,
        extraction_method: str = "text-layer",
        ocr_success: OcrSuccess | None = None,
        ocr_error_code: str | None = None,
    ) -> PdfParseResult:
        ordered = sorted(
            candidates,
            key=lambda item: (
                item.page_number,
                item.bbox[1],
                item.bbox[0],
                item.bbox[3],
                item.bbox[2],
                item.text,
            ),
        )
        text = "\n".join(candidate.text for candidate in ordered)
        page_indexes: dict[int, int] = {}
        blocks: list[DocumentBlock] = []
        offset = 0
        for candidate in ordered:
            page_indexes[candidate.page_number] = page_indexes.get(candidate.page_number, 0) + 1
            block_index = page_indexes[candidate.page_number]
            end = offset + len(candidate.text)
            source = "ocr" if extraction_method == "ocr" else "b"
            blocks.append(
                DocumentBlock(
                    block_id=f"p{candidate.page_number:03d}-{source}{block_index:03d}",
                    locator_type="pdf",
                    char_start=offset,
                    char_end=end,
                    text=candidate.text,
                    page=candidate.page_number,
                    bbox=candidate.bbox,
                )
            )
            offset = end + 1
        return PdfParseResult(
            blocks=blocks,
            text=text,
            page_count=page_count,
            extraction_method=extraction_method,
            ocr_provider=ocr_success.provider if ocr_success else None,
            ocr_model=ocr_success.model if ocr_success else None,
            ocr_version=ocr_success.version if ocr_success else None,
            ocr_error_code=ocr_error_code,
        )

    def _ocr_or_degrade(
        self,
        content: bytes,
        text_layer: PdfParseResult,
        page_count: int,
    ) -> PdfParseResult:
        assert self.ocr_provider is not None
        try:
            result = self.ocr_provider.recognize(OcrRequest(content=content))
        except Exception:
            result = OcrFailure(
                error_code=OcrErrorCode.PROVIDER_FAILURE,
                provider=self.ocr_provider.provider,
                model=self.ocr_provider.model,
                version=self.ocr_provider.version,
            )
        if isinstance(result, OcrSuccess):
            candidates = [
                _PdfCandidate(
                    page_number=block.page_number,
                    bbox=block.bbox.as_tuple(),
                    text=" ".join(block.text.split()),
                )
                for block in result.blocks
            ]
            if candidates:
                return self._result_from_candidates(
                    candidates,
                    result.page_count,
                    extraction_method="ocr",
                    ocr_success=result,
                )
            result = OcrFailure(
                error_code=OcrErrorCode.EMPTY_RESULT,
                provider=result.provider,
                model=result.model,
                version=result.version,
            )

        if text_layer.text:
            return PdfParseResult(
                blocks=text_layer.blocks,
                text=text_layer.text,
                page_count=page_count,
                ocr_error_code=result.error_code.value,
            )
        raise PdfTextUnavailable(result.error_code.value)
