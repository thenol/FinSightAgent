"""Prometheus exposition formatting."""

from app.platform.observability import METRIC_LATENCY, MetricData
from app.platform.prometheus import format_prometheus_metrics


def test_format_prometheus_metrics_aggregates_latency() -> None:
    labels = {
        "stage": "http",
        "operation": "request",
        "method": "GET",
        "route": "/health",
        "status_code": "200",
        "status_class": "2xx",
    }
    rendered = format_prometheus_metrics(
        [
            MetricData(METRIC_LATENCY, 12.0, "ms", labels),
            MetricData(METRIC_LATENCY, 18.0, "ms", labels),
        ]
    )
    assert "finsight_operation_duration_count" in rendered
    assert "finsight_operation_duration_sum" in rendered
    assert 'route="/health"' in rendered
