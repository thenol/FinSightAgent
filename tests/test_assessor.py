"""离线评估测试：固化标注集质量基线，防止规则变更导致质量回退。"""

from app.evaluation.assessor import (
    EVALUATION_VERSION,
    QUALITY_THRESHOLDS,
    Assessor,
    wilson_interval,
)
from app.platform.repository import InMemoryRepository


def test_evaluation_passes_all_quality_thresholds() -> None:
    """标注集上四项指标必须全部达标（MVP 质量门槛）。"""
    report = Assessor(InMemoryRepository()).evaluate()

    assert report.version == EVALUATION_VERSION
    assert report.sample_count >= 25  # 五类事件至少各 5 条
    metric_names = {m.metric for m in report.metrics}
    assert metric_names == {
        "classification_accuracy",
        "entity_alignment_accuracy",
        "key_fields_recall",
        "citation_completeness",
    }
    # 固化当前基线：全部达标
    assert report.overall_passed is True, report.summary()
    for metric in report.metrics:
        assert metric.rate >= metric.threshold, metric.label
        assert metric.lower_bound <= metric.rate <= metric.upper_bound


def test_classification_accuracy_meets_90_percent() -> None:
    report = Assessor(InMemoryRepository()).evaluate()
    cls = next(m for m in report.metrics if m.metric == "classification_accuracy")
    assert cls.rate >= QUALITY_THRESHOLDS["classification_accuracy"]
    assert cls.correct == report.sample_count  # 当前基线：全对


def test_entity_alignment_meets_98_percent() -> None:
    report = Assessor(InMemoryRepository()).evaluate()
    ent = next(m for m in report.metrics if m.metric == "entity_alignment_accuracy")
    assert ent.rate >= QUALITY_THRESHOLDS["entity_alignment_accuracy"]


def test_wilson_interval_bounds() -> None:
    lower, upper = wilson_interval(8, 10)
    assert 0.0 <= lower <= 0.8 <= upper <= 1.0
    # 全对时下界仍应合理（不等于 1.0）
    lower_full, upper_full = wilson_interval(29, 29)
    assert lower_full < 1.0
    assert upper_full == 1.0
    # 零样本不报错
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_evaluation_report_contains_failure_details() -> None:
    """失败样本应记录 details 便于定位。"""
    report = Assessor(InMemoryRepository()).evaluate()
    for sample in report.samples:
        assert sample.sample_id
        assert sample.category in ("positive", "negative", "boundary")
        assert "missing_fields" in sample.details
