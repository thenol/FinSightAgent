import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.evaluation.assessor import Assessor
from app.evaluation.market import (
    ACCEPTANCE_STUB_PROVIDER,
    DeterministicMarketDataProvider,
    FutureDataLeakError,
    MarketBar,
    acceptance_market_payload,
    build_acceptance_market_stub,
    evaluate_market_returns,
)
from app.evaluation.quality import MvpEvaluator
from app.platform.repository import InMemoryRepository

FIXTURE = Path(__file__).parent / "fixtures" / "evaluation" / "mvp-frozen-v1.json"
AS_OF = datetime(2026, 7, 31, 18, tzinfo=timezone.utc)


def test_mvp_frozen_set_reports_metrics_distribution_and_wilson_intervals() -> None:
    report = MvpEvaluator().evaluate_file(FIXTURE)

    assert report.frozen_set.version == "mvp-frozen-v1"
    assert report.frozen_set.reviewers == ("reviewer-alpha", "reviewer-beta")
    assert report.sample_count == 3
    assert report.event_distribution == {
        "earnings_guidance": 1,
        "major_contract": 1,
        "regulatory_penalty": 1,
    }
    assert report.overall_passed
    assert {metric.name for metric in report.metrics} == {
        "citation_completeness",
        "citation_consistency",
        "unsourced_fact_rate",
        "rumor_mislabel_rate",
        "workflow_success_or_explicit_degradation_rate",
        "duplicate_report_rate",
    }
    for metric in report.metrics:
        low, high = metric.confidence_interval
        assert metric.denominator > 0
        assert 0 <= low <= metric.value <= high <= 1


def test_quality_metrics_use_valid_evidence_and_count_duplicate_reports() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["samples"][0]["evidence"][0]["valid"] = False
    payload["samples"][1]["claims"][0]["labeled_as_rumor"] = False
    payload["samples"][2]["report"]["fingerprint"] = "report-002"

    report = MvpEvaluator().evaluate(payload)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["citation_completeness"].numerator == 2
    assert metrics["citation_completeness"].denominator == 3
    assert metrics["unsourced_fact_rate"].numerator == 1
    assert metrics["rumor_mislabel_rate"].numerator == 1
    assert metrics["workflow_success_or_explicit_degradation_rate"].numerator == 3
    assert metrics["duplicate_report_rate"].numerator == 1
    assert not report.overall_passed


def test_assessor_citation_completeness_is_independent_of_classification(tmp_path: Path) -> None:
    fixture = {
        "samples": [
            {
                "id": "citation-not-classification",
                "category": "boundary",
                "title": "公司发布业绩预告",
                "content": "净利润同比增长10%至20%。",
                "source_tier": "S",
                "expected": {
                    "event_type": "unsupported",
                    "entity_codes": [],
                    "key_fields_present": [],
                },
                "evidence": [{"id": "evd-1", "valid": True}],
                "claims": [{"id": "clm-1", "critical": True, "evidence_ids": ["evd-1"]}],
            }
        ]
    }
    path = tmp_path / "samples.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    result = Assessor(InMemoryRepository(), path).evaluate().samples[0]

    assert not result.classification_correct
    assert result.citation_complete
    assert (result.cited_claims, result.critical_claims) == (1, 1)


def test_market_stub_handles_horizons_suspension_missing_and_abnormal_return() -> None:
    days = [date(2026, 7, 1) + timedelta(days=index) for index in range(24)]
    security = [
        _bar("AAA", day, 100 + index, suspended=index == 2)
        for index, day in enumerate(days)
        if index != 4
    ]
    benchmark = [_bar("BENCH", day, 200 + index) for index, day in enumerate(days)]
    provider = DeterministicMarketDataProvider({"AAA": security, "BENCH": benchmark})

    evaluation = evaluate_market_returns(
        provider,
        symbol="AAA",
        benchmark_symbol="BENCH",
        event_day=days[0],
        observation_end=days[-1],
        as_of=AS_OF,
    )
    results = {result.horizon: result for result in evaluation.results}

    assert set(results) == {1, 3, 5, 20}
    assert results[1].end_day == days[1]
    assert results[3].end_day == days[5]  # 停牌 day[2] 与缺失 day[4] 均跳过
    assert results[20].status == "complete"
    expected = (101 / 100 - 1) - (201 / 200 - 1)
    assert results[1].abnormal_return == pytest.approx(expected)
    assert not evaluation.real_market_data
    assert not evaluation.suitable_for_real_market_acceptance


def test_market_stub_rejects_future_available_bar() -> None:
    future_bar = MarketBar(
        symbol="AAA",
        trading_day=date(2026, 7, 2),
        close=Decimal("101"),
        available_at=AS_OF + timedelta(seconds=1),
    )
    provider = DeterministicMarketDataProvider(
        {
            "AAA": [_bar("AAA", date(2026, 7, 1), 100), future_bar],
            "BENCH": [
                _bar("BENCH", date(2026, 7, 1), 200),
                _bar("BENCH", date(2026, 7, 2), 201),
            ],
        }
    )

    with pytest.raises(FutureDataLeakError):
        evaluate_market_returns(
            provider,
            symbol="AAA",
            benchmark_symbol="BENCH",
            event_day=date(2026, 7, 1),
            observation_end=date(2026, 7, 2),
            as_of=AS_OF,
            horizons=(1,),
        )


def test_acceptance_market_stub_is_reproducible_and_never_real() -> None:
    meta = json.loads(
        (Path(__file__).parent / "fixtures" / "market" / "acceptance_stub_meta.json").read_text(
            encoding="utf-8"
        )
    )
    as_of = datetime.fromisoformat(meta["reference_as_of"])
    first = build_acceptance_market_stub(as_of)
    second = build_acceptance_market_stub(as_of)
    payload = acceptance_market_payload(first)

    assert first == second
    assert first.provider_name == meta["provider"] == ACCEPTANCE_STUB_PROVIDER
    assert first.real_market_data is False
    assert first.suitable_for_real_market_acceptance is False
    assert payload["real_market_data"] is False
    assert payload["suitable_for_real_market_acceptance"] is False
    assert [item["horizon"] for item in payload["horizons"]] == meta["horizons"]
    assert all(item["status"] == "complete" for item in payload["horizons"])


def _bar(symbol: str, trading_day: date, close: int, *, suspended: bool = False) -> MarketBar:
    return MarketBar(
        symbol=symbol,
        trading_day=trading_day,
        close=Decimal(close),
        available_at=datetime.combine(
            trading_day,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ),
        suspended=suspended,
    )
