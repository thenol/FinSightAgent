"""Versioned OCR provider contract and deterministic local stub."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Union, runtime_checkable

OCR_CONTRACT_VERSION = "ocr-contract-v1"
DETERMINISTIC_PROVIDER = "deterministic-local"
DETERMINISTIC_MODEL = "fixture-map"
DETERMINISTIC_VERSION = "1"


class OcrErrorCode(str, Enum):
    """Stable error codes callers may persist and replay."""

    UNSUPPORTED_DOCUMENT = "OCR_UNSUPPORTED_DOCUMENT"
    PROVIDER_FAILURE = "OCR_PROVIDER_FAILURE"
    EMPTY_RESULT = "OCR_EMPTY_RESULT"
    INVALID_RESULT = "OCR_INVALID_RESULT"


@dataclass(frozen=True)
class NormalizedBBox:
    """A page-relative bounding box with coordinates in the inclusive [0, 1] range."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        values = (self.x0, self.y0, self.x1, self.y1)
        if not all(0.0 <= value <= 1.0 for value in values):
            raise ValueError("OCR_BBOX_OUT_OF_RANGE")
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("OCR_BBOX_INVALID_ORDER")

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True)
class OcrRequest:
    """Structured input passed across the OCR provider boundary."""

    content: bytes
    media_type: str = "application/pdf"
    contract_version: str = OCR_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != OCR_CONTRACT_VERSION:
            raise ValueError("OCR_CONTRACT_VERSION_UNSUPPORTED")
        if not self.content:
            raise ValueError("OCR_DOCUMENT_EMPTY")

    @property
    def document_sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class OcrTextBlock:
    """One OCR text block with a stable page locator."""

    page_number: int
    bbox: NormalizedBBox
    text: str
    confidence: float

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("OCR_PAGE_NUMBER_INVALID")
        if not self.text.strip():
            raise ValueError("OCR_BLOCK_TEXT_EMPTY")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("OCR_CONFIDENCE_OUT_OF_RANGE")


@dataclass(frozen=True)
class OcrSuccess:
    """Successful, non-empty OCR response."""

    blocks: tuple[OcrTextBlock, ...]
    page_count: int
    provider: str
    model: str
    version: str
    contract_version: str = OCR_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != OCR_CONTRACT_VERSION:
            raise ValueError("OCR_CONTRACT_VERSION_UNSUPPORTED")
        if self.page_count < 1:
            raise ValueError("OCR_PAGE_COUNT_INVALID")
        if not self.blocks:
            raise ValueError("OCR_EMPTY_RESULT")
        if any(block.page_number > self.page_count for block in self.blocks):
            raise ValueError("OCR_PAGE_OUT_OF_RANGE")
        if not self.provider or not self.model or not self.version:
            raise ValueError("OCR_PROVIDER_IDENTITY_REQUIRED")


@dataclass(frozen=True)
class OcrFailure:
    """Deterministic OCR failure; failures never masquerade as empty success."""

    error_code: OcrErrorCode
    provider: str
    model: str
    version: str
    retryable: bool = False
    contract_version: str = OCR_CONTRACT_VERSION


OcrResult = Union[OcrSuccess, OcrFailure]


@runtime_checkable
class OcrProvider(Protocol):
    """Explicit v1 provider interface."""

    contract_version: str
    provider: str
    model: str
    version: str

    def recognize(self, request: OcrRequest) -> OcrResult: ...


@dataclass(frozen=True)
class DeterministicOcrFixture:
    """Digest-addressed response used by the local deterministic provider."""

    document_sha256: str
    page_count: int
    blocks: tuple[OcrTextBlock, ...]


class DeterministicOcrProvider:
    """Local OCR stub returning predeclared results for exact document bytes."""

    contract_version = OCR_CONTRACT_VERSION
    provider = DETERMINISTIC_PROVIDER
    model = DETERMINISTIC_MODEL
    version = DETERMINISTIC_VERSION

    def __init__(
        self,
        fixtures: Sequence[DeterministicOcrFixture] = (),
        *,
        failure_code: OcrErrorCode = OcrErrorCode.UNSUPPORTED_DOCUMENT,
    ) -> None:
        self._fixtures = {fixture.document_sha256: fixture for fixture in fixtures}
        self._failure_code = failure_code

    def recognize(self, request: OcrRequest) -> OcrResult:
        fixture = self._fixtures.get(request.document_sha256)
        if fixture is None:
            return OcrFailure(
                error_code=self._failure_code,
                provider=self.provider,
                model=self.model,
                version=self.version,
            )
        try:
            return OcrSuccess(
                blocks=fixture.blocks,
                page_count=fixture.page_count,
                provider=self.provider,
                model=self.model,
                version=self.version,
            )
        except ValueError:
            return OcrFailure(
                error_code=OcrErrorCode.INVALID_RESULT,
                provider=self.provider,
                model=self.model,
                version=self.version,
            )
