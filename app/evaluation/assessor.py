"""离线评估模块。

对 EventClassifier、EntityResolver、EvidenceService 在标注集上跑离线评估，
输出四项质量指标 + 样本量 + 95% 置信区间（Wilson 区间），用于 MVP 质量门槛校准。

指标：
- 分类准确率：event_type 是否匹配期望
- 实体对齐准确率：解析出的 market_code 集合是否匹配期望
- key_fields 召回率：期望字段是否被抽到
- 引用完整率：生成的 Claim 是否关联 Evidence

评估尊重 as_of 时间截面，不引入标注样本之外的信息。规则版本化以便回放。
"""

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domain import Document
from app.events.classifier import EventClassifier
from app.events.entities import EntityResolver
from app.platform.repository import Repository

EVALUATION_VERSION = "evaluation-v1"
FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "labeled_events" / "samples.json"
)

# MVP 质量门槛（DD-05 §6）
QUALITY_THRESHOLDS = {
    "classification_accuracy": 0.90,
    "entity_alignment_accuracy": 0.98,
    "key_fields_recall": 0.85,
    "citation_completeness": 1.00,
}


@dataclass(frozen=True)
class SampleResult:
    sample_id: str
    category: str
    expected_event_type: str
    actual_event_type: str
    classification_correct: bool
    entity_correct: bool
    key_fields_recall: float
    citation_complete: bool
    cited_claims: int = 0
    critical_claims: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricReport:
    metric: str
    correct: int
    total: int
    rate: float
    lower_bound: float
    upper_bound: float
    threshold: float
    passed: bool

    @property
    def label(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"{self.metric}: {self.rate:.1%} ({self.correct}/{self.total}), "
            f"95% CI [{self.lower_bound:.1%}, {self.upper_bound:.1%}], "
            f"门槛 {self.threshold:.0%} -> {status}"
        )


@dataclass(frozen=True)
class EvaluationReport:
    version: str
    generated_at: str
    sample_count: int
    event_distribution: dict[str, int]
    metrics: list[MetricReport]
    samples: list[SampleResult]
    overall_passed: bool

    def summary(self) -> str:
        lines = [f"评估报告 v{self.version}（{self.sample_count} 样本，{self.generated_at}）"]
        for metric in self.metrics:
            lines.append(f"  {metric.label}")
        lines.append(f"  总体: {'PASS' if self.overall_passed else 'FAIL'}")
        return "\n".join(lines)


class Assessor:
    """在标注集上跑离线评估。"""

    def __init__(self, repository: Repository, fixture_path: Path | None = None) -> None:
        self.repository = repository
        self.classifier = EventClassifier()
        self.resolver = EntityResolver(repository)
        self.fixture_path = fixture_path or FIXTURE_PATH

    def load_samples(self) -> list[dict[str, Any]]:
        data = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        return data["samples"]

    def evaluate(self, now: datetime | None = None) -> EvaluationReport:
        samples = self.load_samples()
        results = [self._evaluate_sample(sample) for sample in samples]

        classification = self._metric(
            "classification_accuracy", [r.classification_correct for r in results]
        )
        entity = self._metric(
            "entity_alignment_accuracy",
            [
                r.entity_correct
                for r in results
                if r.expected_event_type
                not in {"unsupported", "general_market_news", "out_of_scope"}
            ],
        )
        key_fields = self._metric(
            "key_fields_recall",
            [
                r.key_fields_recall >= 1.0
                for r in results
                if r.expected_event_type
                not in {"unsupported", "general_market_news", "out_of_scope"}
            ],
        )
        citation_outcomes = [
            index < result.cited_claims
            for result in results
            for index in range(result.critical_claims)
        ]
        citation = self._metric("citation_completeness", citation_outcomes)

        metrics = [classification, entity, key_fields, citation]
        return EvaluationReport(
            version=EVALUATION_VERSION,
            generated_at=(now or datetime.now(timezone.utc)).isoformat(),
            sample_count=len(samples),
            event_distribution=dict(
                sorted(Counter(result.expected_event_type for result in results).items())
            ),
            metrics=metrics,
            samples=results,
            overall_passed=all(m.passed for m in metrics),
        )

    def _evaluate_sample(self, sample: dict[str, Any]) -> SampleResult:
        document = self._make_document(sample)
        expected = sample["expected"]

        # 分类
        classification = self.classifier.classify(document)
        actual_event = classification.event_type
        classification_correct = actual_event == expected["event_type"]

        # 实体对齐
        document_text = f"{document.title}\n{document.content}"
        resolutions = self.resolver.resolve(document_text, document.id)
        actual_codes = {r.market_code for r in resolutions}
        expected_codes = set(expected["entity_codes"])
        # 实体正确：实际集合 == 期望集合（无期望时要求实际为空）
        entity_correct = actual_codes == expected_codes

        # key_fields 召回
        expected_fields = set(expected["key_fields_present"])
        actual_fields = set(classification.key_fields.keys())
        if expected_fields:
            recall = len(expected_fields & actual_fields) / len(expected_fields)
        else:
            recall = 1.0

        # 引用完整率必须按关键 Claim→Evidence 关系计算，不能用分类正确率代替。
        # 新标注可显式提供 claims/evidence；旧 labeled_events fixture 则把每个抽取字段
        # 视为一个 Claim，并以当前文档快照作为 Evidence，维持原评估入口兼容。
        cited_claims, critical_claims, missing_citations = self._citation_counts(
            sample, actual_fields, expected_fields, document
        )
        citation_complete = cited_claims == critical_claims

        return SampleResult(
            sample_id=sample["id"],
            category=sample["category"],
            expected_event_type=expected["event_type"],
            actual_event_type=actual_event,
            classification_correct=classification_correct,
            entity_correct=entity_correct,
            key_fields_recall=recall,
            citation_complete=citation_complete,
            cited_claims=cited_claims,
            critical_claims=critical_claims,
            details={
                "missing_fields": sorted(expected_fields - actual_fields),
                "missing_citations": missing_citations,
                "actual_codes": sorted(actual_codes),
                "missing_required": classification.missing_required,
            },
        )

    @staticmethod
    def _citation_counts(
        sample: dict[str, Any],
        actual_fields: set[str],
        expected_fields: set[str],
        document: Document,
    ) -> tuple[int, int, list[str]]:
        claims = sample.get("claims")
        if claims is not None:
            evidence_ids = {
                str(item["id"])
                for item in sample.get("evidence", [])
                if item.get("id") and item.get("valid", True)
            }
            critical = [claim for claim in claims if claim.get("critical", True)]
            cited = [
                claim
                for claim in critical
                if any(
                    str(evidence_id) in evidence_ids
                    for evidence_id in claim.get("evidence_ids", [])
                )
            ]
            missing = [
                str(claim.get("id", f"claim-{index}"))
                for index, claim in enumerate(critical)
                if claim not in cited
            ]
            return len(cited), len(critical), missing

        critical_fields = expected_fields
        if not critical_fields:
            return 0, 0, []
        has_evidence = bool(document.content.strip())
        cited_fields = critical_fields & actual_fields if has_evidence else set()
        return (
            len(cited_fields),
            len(critical_fields),
            sorted(critical_fields - cited_fields),
        )

    @staticmethod
    def _make_document(sample: dict[str, Any]) -> Document:
        return Document(
            id=sample["id"],
            source_id="eval",
            source_tier=sample["source_tier"],
            external_id=sample["id"],
            canonical_url=None,
            title=sample["title"],
            content=sample["content"],
            content_hash=hash(sample["content"]),
            published_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
            ingested_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        )

    def _metric(self, name: str, outcomes: list[bool]) -> MetricReport:
        total = len(outcomes)
        correct = sum(1 for ok in outcomes if ok)
        rate = correct / total if total else 0.0
        lower, upper = wilson_interval(correct, total)
        threshold = QUALITY_THRESHOLDS.get(name, 0.0)
        return MetricReport(
            metric=name,
            correct=correct,
            total=total,
            rate=rate,
            lower_bound=lower,
            upper_bound=upper,
            threshold=threshold,
            passed=rate >= threshold,
        )


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% 置信区间，避免 total=0 或极端比例时退化。"""
    if total == 0:
        return 0.0, 0.0
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)
