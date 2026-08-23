"""Dependency-free Prometheus text exposition for observability metrics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from app.platform.observability import (
    METRIC_LATENCY,
    METRIC_SUCCESS,
    MetricData,
)


def _prometheus_name(name: str) -> str:
    return name.replace(".", "_")


def _label_string(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{key}="{value}"' for key, value in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def format_prometheus_metrics(metrics: Iterable[MetricData]) -> str:
    """Render collected metrics in Prometheus text exposition format."""
    latency: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
    success: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
    gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    for metric in metrics:
        label_items = tuple(sorted(metric.labels.items()))
        key = (metric.name, label_items)
        if metric.name == METRIC_LATENCY:
            latency[key].append(metric.value)
        elif metric.name == METRIC_SUCCESS:
            success[key].append(metric.value)
        else:
            gauges[key] = metric.value

    lines: list[str] = []
    for (name, label_items), values in sorted(latency.items()):
        prom_name = _prometheus_name(name)
        labels = dict(label_items)
        label_text = _label_string(labels)
        total = sum(values)
        count = len(values)
        lines.append(f"{prom_name}_count{label_text} {count}")
        lines.append(f"{prom_name}_sum{label_text} {total}")
        if count:
            lines.append(f"{prom_name}_avg{label_text} {total / count:.6f}")

    for (name, label_items), values in sorted(success.items()):
        prom_name = _prometheus_name(name)
        labels = dict(label_items)
        label_text = _label_string(labels)
        total = sum(values)
        count = len(values)
        lines.append(f"{prom_name}_total{label_text} {total}")
        lines.append(f"{prom_name}_count{label_text} {count}")

    for (name, label_items), value in sorted(gauges.items()):
        prom_name = _prometheus_name(name)
        label_text = _label_string(dict(label_items))
        lines.append(f"{prom_name}{label_text} {value}")

    if not lines:
        return "# no metrics collected yet\n"
    return "\n".join(lines) + "\n"
