"""Local quality-contract evidence for DOC05 citation/rumor gates.

These metrics exercise the evaluator against a deterministic fixture. They are
**not** production human labels and must not flip ``mvp_acceptance`` gates to PASS.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.evaluation.quality import MvpEvaluator

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "evaluation"
    / "local-quality-contract-v1.json"
)

BLOCKED_PRODUCTION_GATES = (
    "DOC05-Q-CITATION-CONSISTENCY",
    "DOC05-Q-UNSOURCED-FACTS",
    "DOC05-Q-RUMOR-MISLABEL",
)

CONTRACT_METRICS = (
    "citation_consistency",
    "unsourced_fact_rate",
    "rumor_mislabel_rate",
)


def evaluate_local_quality_contract(
    fixture_path: Path | None = None,
) -> dict[str, Any]:
    """Run local contract metrics and explicitly mark them non-production."""

    path = fixture_path or DEFAULT_FIXTURE
    report = MvpEvaluator().evaluate_file(path)
    metrics = {
        metric.name: {
            "numerator": metric.numerator,
            "denominator": metric.denominator,
            "value": metric.value,
            "threshold": metric.threshold,
            "direction": metric.direction,
            "passed": metric.passed,
            "confidence_interval_95": list(metric.confidence_interval),
        }
        for metric in report.metrics
        if metric.name in CONTRACT_METRICS
    }
    return {
        "version": report.frozen_set.version,
        "fixture": str(path),
        "sample_count": report.sample_count,
        "event_distribution": report.event_distribution,
        "metrics": metrics,
        "local_contract_passed": all(item["passed"] for item in metrics.values()),
        "real_human_labels": False,
        "suitable_for_production_acceptance": False,
        "blocked_production_gates": list(BLOCKED_PRODUCTION_GATES),
        "frozen_set": asdict(report.frozen_set),
    }
