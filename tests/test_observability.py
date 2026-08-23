import json
import logging

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from app.main import JsonFormatter, create_app
from app.platform.observability import (
    METRIC_CITATION_COMPLETENESS,
    METRIC_COST,
    METRIC_HUMAN_REVIEW,
    METRIC_LATENCY,
    METRIC_SUCCESS,
    InMemoryObservabilitySink,
    MetricData,
    MetricSink,
    Observability,
    TraceContext,
    Tracer,
)


def _observability() -> tuple[Observability, InMemoryObservabilitySink]:
    sink = InMemoryObservabilitySink()
    return Observability(sink, sink), sink


def test_in_memory_sink_implements_explicit_contracts_and_stage_chain() -> None:
    observability, sink = _observability()
    assert isinstance(sink, MetricSink)
    assert isinstance(sink, Tracer)

    with observability.stage("collection", "fetch") as collection:
        with observability.stage("event", "classify", parent=collection.context) as event:
            with observability.stage("workflow", "research", parent=event.context):
                pass

    assert [span.stage for span in sink.spans] == ["workflow", "event", "collection"]
    assert sink.spans[1].parent_span_id == sink.spans[2].context.span_id
    assert sink.spans[0].context.trace_id == sink.spans[2].context.trace_id


def test_supported_metrics_and_low_cardinality_label_policy() -> None:
    observability, sink = _observability()
    labels = {"stage": "report", "operation": "publish", "outcome": "ok"}

    observability.latency(12.5, **labels)
    observability.cost(0.02, stage="model", operation="generate", currency="USD")
    observability.success(True, **labels)
    observability.human_review(False, **labels)
    observability.citation_completeness(0.75, **labels)

    assert {metric.name for metric in sink.metrics} == {
        METRIC_LATENCY,
        METRIC_COST,
        METRIC_SUCCESS,
        METRIC_HUMAN_REVIEW,
        METRIC_CITATION_COMPLETENESS,
    }
    with pytest.raises(ValueError, match="event_id"):
        MetricData("unsafe", 1, "count", {"event_id": "evt-unique"})
    with pytest.raises(ValueError, match="document_id"):
        observability.success(True, document_id="doc-unique")


def test_spans_and_logs_drop_sensitive_payloads_but_keep_request_id() -> None:
    observability, sink = _observability()
    with observability.stage(
        "model",
        "generate",
        attributes={
            "request_id": "req-safe",
            "prompt": "private prompt",
            "api_key": "key-value",
            "event_id": "evt-unique",
        },
    ):
        pass

    assert sink.spans[0].attributes == {"request_id": "req-safe"}

    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        'authorization=Bearer super-secret password=hunter2 {"api_key": "json-secret"}',
        (),
        None,
    )
    record.request_id = "req-safe"
    record.body = "private body"
    payload = json.loads(JsonFormatter().format(record))
    rendered = json.dumps(payload)
    assert payload["request_id"] == "req-safe"
    assert "super-secret" not in rendered
    assert "hunter2" not in rendered
    assert "json-secret" not in rendered
    assert "private body" not in rendered


def test_http_trace_context_latency_and_error_metrics() -> None:
    observability, sink = _observability()
    application = create_app(observability)

    @application.get("/observability-test")
    async def unavailable() -> Response:
        return Response(status_code=503)

    parent = TraceContext.new()
    with TestClient(application) as client:
        response = client.get(
            "/observability-test",
            headers={"traceparent": parent.as_traceparent(), "X-Request-ID": "req-http"},
        )

    assert response.status_code == 503
    assert response.headers["X-Request-ID"] == "req-http"
    returned = TraceContext.from_traceparent(response.headers["traceparent"])
    assert returned is not None
    assert returned.trace_id == parent.trace_id

    http_span = next(span for span in sink.spans if span.stage == "http")
    assert http_span.parent_span_id == parent.span_id
    assert http_span.attributes["http.route"] == "/observability-test"
    latency = next(metric for metric in sink.metrics if metric.name == METRIC_LATENCY)
    success = next(metric for metric in sink.metrics if metric.name == METRIC_SUCCESS)
    assert latency.value >= 0
    assert latency.labels["route"] == "/observability-test"
    assert success.value == 0


def test_metrics_endpoint_exports_prometheus_text(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_METRICS_ENABLED", "true")
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    with TestClient(create_app()) as client:
        client.get("/health")
        metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "finsight_operation_duration" in metrics.text
