"""M4 本地质量评估契约。

比例指标保留分子、分母、事件分布和 Wilson 95% 置信区间。输入是冻结的
人工标注快照，不读取生产状态，也不让分类结果充当引用质量标签。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.evaluation.assessor import wilson_interval

MVP_EVALUATION_VERSION = "mvp-evaluation-v1"

QUALITY_THRESHOLDS = {
    "citation_completeness": (1.0, "min"),
    "citation_consistency": (1.0, "min"),
    "unsourced_fact_rate": (0.0, "max"),
    "rumor_mislabel_rate": (0.0, "max"),
    "workflow_success_or_explicit_degradation_rate": (0.98, "min"),
    "duplicate_report_rate": (0.0, "max"),
}


@dataclass(frozen=True)
class FrozenSetMetadata:
    """不可变验收集的人工复核记录。"""

    version: str
    reviewers: tuple[str, ...]
    reviewed_at: datetime

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> FrozenSetMetadata:
        version = str(value.get("version", "")).strip()
        reviewers = tuple(
            str(item).strip()
            for item in value.get("reviewers", ())
            if str(item).strip()
        )
        raw_reviewed_at = str(value.get("reviewed_at", ""))
        try:
            reviewed_at = datetime.fromisoformat(raw_reviewed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("frozen_set.reviewed_at must be an ISO-8601 datetime") from exc
        if not version:
            raise ValueError("frozen_set.version is required")
        if not reviewers:
            raise ValueError("frozen_set.reviewers must contain at least one reviewer")
        if reviewed_at.tzinfo is None:
            raise ValueError("frozen_set.reviewed_at must include a timezone")
        return cls(version=version, reviewers=reviewers, reviewed_at=reviewed_at)


@dataclass(frozen=True)
class ClaimAssessment:
    id: str
    is_fact: bool
    is_critical: bool
    evidence_ids: tuple[str, ...]
    supported_evidence_ids: tuple[str, ...]
    is_rumor: bool
    labeled_as_rumor: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ClaimAssessment:
        evidence_ids = tuple(str(item) for item in value.get("evidence_ids", ()))
        supported = tuple(str(item) for item in value.get("supported_evidence_ids", ()))
        unknown = set(supported) - set(evidence_ids)
        if unknown:
            raise ValueError(f"supported evidence must be cited: {sorted(unknown)}")
        return cls(
            id=str(value["id"]),
            is_fact=bool(value.get("is_fact", True)),
            is_critical=bool(value.get("is_critical", True)),
            evidence_ids=evidence_ids,
            supported_evidence_ids=supported,
            is_rumor=bool(value.get("is_rumor", False)),
            labeled_as_rumor=bool(value.get("labeled_as_rumor", False)),
        )


@dataclass(frozen=True)
class EvaluationSample:
    id: str
    event_type: str
    claims: tuple[ClaimAssessment, ...]
    valid_evidence_ids: frozenset[str]
    workflow_started: bool
    workflow_status: str
    explicitly_degraded: bool
    report_fingerprint: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> EvaluationSample:
        workflow = value.get("workflow", {})
        report = value.get("report")
        fingerprint = report.get("fingerprint") if isinstance(report, Mapping) else None
        valid_evidence_ids = frozenset(
            str(item["id"])
            for item in value.get("evidence", ())
            if item.get("id") and item.get("valid", True)
        )
        return cls(
            id=str(value["id"]),
            event_type=str(value["event_type"]),
            claims=tuple(ClaimAssessment.from_mapping(item) for item in value.get("claims", ())),
            valid_evidence_ids=valid_evidence_ids,
            workflow_started=bool(workflow.get("started", False)),
            workflow_status=str(workflow.get("status", "not_started")),
            explicitly_degraded=bool(workflow.get("explicitly_degraded", False)),
            report_fingerprint=str(fingerprint) if fingerprint else None,
        )


@dataclass(frozen=True)
class QualityMetric:
    name: str
    numerator: int
    denominator: int
    value: float
    confidence_interval: tuple[float, float]
    threshold: float
    direction: str
    passed: bool


@dataclass(frozen=True)
class MvpEvaluationReport:
    version: str
    frozen_set: FrozenSetMetadata
    sample_count: int
    event_distribution: dict[str, int]
    metrics: tuple[QualityMetric, ...]
    overall_passed: bool


class MvpEvaluator:
    """聚合冻结样本中的引用、事实、运行和报告质量指标。"""

    def __init__(
        self,
        thresholds: Mapping[str, tuple[float, str]] | None = None,
    ) -> None:
        self.thresholds = dict(thresholds or QUALITY_THRESHOLDS)

    def evaluate_file(self, path: Path) -> MvpEvaluationReport:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return self.evaluate(payload)

    def evaluate(self, payload: Mapping[str, Any]) -> MvpEvaluationReport:
        metadata = FrozenSetMetadata.from_mapping(payload.get("frozen_set", {}))
        samples = tuple(EvaluationSample.from_mapping(item) for item in payload.get("samples", ()))
        event_distribution = dict(sorted(Counter(item.event_type for item in samples).items()))

        critical_claims = [
            (claim, sample.valid_evidence_ids)
            for sample in samples
            for claim in sample.claims
            if claim.is_critical
        ]
        factual_claims = [
            (claim, sample.valid_evidence_ids)
            for sample in samples
            for claim in sample.claims
            if claim.is_fact
        ]
        citations = [
            evidence_id
            for claim, _ in factual_claims
            for evidence_id in claim.evidence_ids
        ]
        supported_citations = [
            evidence_id
            for claim, valid_evidence in factual_claims
            for evidence_id in claim.supported_evidence_ids
            if evidence_id in valid_evidence
        ]
        rumors = [claim for sample in samples for claim in sample.claims if claim.is_rumor]
        workflows = [sample for sample in samples if sample.workflow_started]
        fingerprints = [
            sample.report_fingerprint
            for sample in samples
            if sample.report_fingerprint
        ]

        pairs = (
            (
                "citation_completeness",
                sum(
                    bool(set(claim.evidence_ids) & valid_evidence)
                    for claim, valid_evidence in critical_claims
                ),
                len(critical_claims),
            ),
            ("citation_consistency", len(supported_citations), len(citations)),
            (
                "unsourced_fact_rate",
                sum(
                    not (set(claim.evidence_ids) & valid_evidence)
                    for claim, valid_evidence in factual_claims
                ),
                len(factual_claims),
            ),
            (
                "rumor_mislabel_rate",
                sum(not claim.labeled_as_rumor for claim in rumors),
                len(rumors),
            ),
            (
                "workflow_success_or_explicit_degradation_rate",
                sum(
                    sample.workflow_status == "succeeded" or sample.explicitly_degraded
                    for sample in workflows
                ),
                len(workflows),
            ),
            (
                "duplicate_report_rate",
                _duplicate_count(fingerprints),
                len(fingerprints),
            ),
        )
        metrics = tuple(self._metric(*pair) for pair in pairs)
        return MvpEvaluationReport(
            version=MVP_EVALUATION_VERSION,
            frozen_set=metadata,
            sample_count=len(samples),
            event_distribution=event_distribution,
            metrics=metrics,
            overall_passed=all(metric.passed for metric in metrics),
        )

    def _metric(self, name: str, numerator: int, denominator: int) -> QualityMetric:
        value = numerator / denominator if denominator else 0.0
        threshold, direction = self.thresholds[name]
        passed = value >= threshold if direction == "min" else value <= threshold
        return QualityMetric(
            name=name,
            numerator=numerator,
            denominator=denominator,
            value=value,
            confidence_interval=wilson_interval(numerator, denominator),
            threshold=threshold,
            direction=direction,
            passed=passed,
        )


def _duplicate_count(fingerprints: Iterable[str]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for fingerprint in fingerprints:
        if fingerprint in seen:
            duplicates += 1
        else:
            seen.add(fingerprint)
    return duplicates
