"""Dependency-free observability contracts and an in-memory test implementation."""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

STAGES = ("collection", "event", "workflow", "model", "tool", "report", "http")

METRIC_LATENCY = "finsight.operation.duration"
METRIC_COST = "finsight.operation.cost"
METRIC_SUCCESS = "finsight.operation.success"
METRIC_HUMAN_REVIEW = "finsight.human_review.required"
METRIC_CITATION_COMPLETENESS = "finsight.report.citation_completeness"

ALLOWED_METRIC_LABELS = frozenset(
    {
        "stage",
        "operation",
        "method",
        "route",
        "status_code",
        "status_class",
        "outcome",
        "provider",
        "model",
        "tool",
        "currency",
    }
)
FORBIDDEN_ATTRIBUTE_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "content",
        "body",
        "prompt",
        "completion",
        "document_id",
        "event_id",
    }
)
_TRACEPARENT = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace>[0-9a-f]{32})-"
    r"(?P<span>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _immutable(values: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(values or {}))


def _valid_hex_id(value: str) -> bool:
    return any(character != "0" for character in value)


@dataclass(frozen=True)
class TraceContext:
    """Minimal W3C trace context, independent of a tracing SDK."""

    trace_id: str
    span_id: str
    sampled: bool = True

    @classmethod
    def new(cls, *, trace_id: str | None = None, sampled: bool = True) -> TraceContext:
        return cls(
            trace_id=trace_id or secrets.token_hex(16),
            span_id=secrets.token_hex(8),
            sampled=sampled,
        )

    @classmethod
    def from_traceparent(cls, value: str | None) -> TraceContext | None:
        match = _TRACEPARENT.fullmatch((value or "").strip().lower())
        if not match or match.group("version") == "ff":
            return None
        trace_id = match.group("trace")
        span_id = match.group("span")
        if not _valid_hex_id(trace_id) or not _valid_hex_id(span_id):
            return None
        return cls(
            trace_id=trace_id,
            span_id=span_id,
            sampled=bool(int(match.group("flags"), 16) & 1),
        )

    def child(self) -> TraceContext:
        return TraceContext.new(trace_id=self.trace_id, sampled=self.sampled)

    def as_traceparent(self) -> str:
        flags = "01" if self.sampled else "00"
        return f"00-{self.trace_id}-{self.span_id}-{flags}"


@dataclass(frozen=True)
class MetricData:
    name: str
    value: float
    unit: str
    labels: Mapping[str, str] = field(default_factory=dict)
    recorded_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        unknown = set(self.labels) - ALLOWED_METRIC_LABELS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"Metric labels are not low-cardinality: {names}")
        object.__setattr__(self, "labels", _immutable(self.labels))


@dataclass(frozen=True)
class SpanData:
    name: str
    stage: str
    context: TraceContext
    parent_span_id: str | None
    started_at: datetime
    ended_at: datetime
    duration_ms: float
    status: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    error_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", _immutable(self.attributes))


@runtime_checkable
class MetricSink(Protocol):
    def record_metric(self, metric: MetricData) -> None:
        """Record one already-structured metric."""


@runtime_checkable
class Span(Protocol):
    @property
    def context(self) -> TraceContext:
        """Context propagated to child stages."""

    def set_attribute(self, key: str, value: Any) -> None:
        """Attach a safe, bounded attribute."""

    def set_status(self, status: str) -> None:
        """Mark a completed operation as ok or error."""

    def __enter__(self) -> Span:
        """Start the span."""

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        """Finish the span and never suppress application exceptions."""


@runtime_checkable
class Tracer(Protocol):
    def start_span(
        self,
        name: str,
        *,
        stage: str,
        parent: TraceContext | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Span:
        """Create a stage span. Future OpenTelemetry adapters implement this contract."""


def _safe_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        normalized = key.lower().replace("-", "_")
        if any(part in normalized for part in FORBIDDEN_ATTRIBUTE_PARTS):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
    return safe


class _InMemorySpan(AbstractContextManager["_InMemorySpan"]):
    def __init__(
        self,
        sink: InMemoryObservabilitySink,
        name: str,
        stage: str,
        parent: TraceContext | None,
        attributes: Mapping[str, Any] | None,
    ) -> None:
        self._sink = sink
        self._name = name
        self._stage = stage
        self._parent = parent
        self._context = parent.child() if parent else TraceContext.new()
        self._attributes = _safe_attributes(attributes)
        self._started_at: datetime | None = None
        self._started_clock: float | None = None
        self._status = "ok"

    @property
    def context(self) -> TraceContext:
        return self._context

    def set_attribute(self, key: str, value: Any) -> None:
        self._attributes.update(_safe_attributes({key: value}))

    def set_status(self, status: str) -> None:
        if status not in {"ok", "error"}:
            raise ValueError("Span status must be 'ok' or 'error'")
        self._status = status

    def __enter__(self) -> _InMemorySpan:
        self._started_at = _utc_now()
        self._started_clock = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        ended_at = _utc_now()
        started_at = self._started_at or ended_at
        started_clock = self._started_clock or time.perf_counter()
        error_type = exc_type.__name__ if exc_type is not None else None
        self._sink.record_span(
            SpanData(
                name=self._name,
                stage=self._stage,
                context=self._context,
                parent_span_id=self._parent.span_id if self._parent else None,
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=max(0.0, (time.perf_counter() - started_clock) * 1000),
                status="error" if exc_type is not None else self._status,
                attributes=self._attributes,
                error_type=error_type,
            )
        )
        return False


class InMemoryObservabilitySink(MetricSink, Tracer):
    """Thread-safe test sink. Do not use it as an unbounded production backend."""

    def __init__(self) -> None:
        self.metrics: list[MetricData] = []
        self.spans: list[SpanData] = []
        self._lock = Lock()

    def record_metric(self, metric: MetricData) -> None:
        with self._lock:
            self.metrics.append(metric)

    def record_span(self, span: SpanData) -> None:
        with self._lock:
            self.spans.append(span)

    def start_span(
        self,
        name: str,
        *,
        stage: str,
        parent: TraceContext | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Span:
        return _InMemorySpan(self, name, stage, parent, attributes)


class NoOpObservabilitySink(MetricSink, Tracer):
    """Safe default that keeps the API usable without an external service."""

    def record_metric(self, metric: MetricData) -> None:
        return None

    def start_span(
        self,
        name: str,
        *,
        stage: str,
        parent: TraceContext | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Span:
        return _NoOpSpan(parent)


class _NoOpSpan(AbstractContextManager["_NoOpSpan"]):
    def __init__(self, parent: TraceContext | None) -> None:
        self._context = parent.child() if parent else TraceContext.new()

    @property
    def context(self) -> TraceContext:
        return self._context

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_status(self, status: str) -> None:
        return None

    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        return False


class Observability:
    """Reusable stage API for collection through report generation."""

    def __init__(self, metric_sink: MetricSink, tracer: Tracer) -> None:
        self.metric_sink = metric_sink
        self.tracer = tracer

    @classmethod
    def no_op(cls) -> Observability:
        sink = NoOpObservabilitySink()
        return cls(sink, sink)

    def stage(
        self,
        stage: str,
        operation: str,
        *,
        parent: TraceContext | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Span:
        if stage not in STAGES:
            raise ValueError(f"Unknown observability stage: {stage}")
        return self.tracer.start_span(
            f"{stage}.{operation}",
            stage=stage,
            parent=parent,
            attributes=attributes,
        )

    def latency(self, duration_ms: float, **labels: str) -> None:
        self._metric(METRIC_LATENCY, duration_ms, "ms", labels)

    def cost(self, amount: float, **labels: str) -> None:
        currency = labels.setdefault("currency", "USD")
        self._metric(METRIC_COST, amount, currency, labels)

    def success(self, succeeded: bool, **labels: str) -> None:
        self._metric(METRIC_SUCCESS, float(succeeded), "ratio", labels)

    def human_review(self, required: bool, **labels: str) -> None:
        self._metric(METRIC_HUMAN_REVIEW, float(required), "ratio", labels)

    def citation_completeness(self, ratio: float, **labels: str) -> None:
        if not 0.0 <= ratio <= 1.0:
            raise ValueError("Citation completeness must be between 0 and 1")
        self._metric(METRIC_CITATION_COMPLETENESS, ratio, "ratio", labels)

    def _metric(self, name: str, value: float, unit: str, labels: Mapping[str, str]) -> None:
        self.metric_sink.record_metric(
            MetricData(name=name, value=float(value), unit=unit, labels=labels)
        )
