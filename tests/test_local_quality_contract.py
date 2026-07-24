from pathlib import Path

from app.evaluation.local_quality_contract import (
    BLOCKED_PRODUCTION_GATES,
    evaluate_local_quality_contract,
)

FIXTURE = (
    Path(__file__).parent / "fixtures" / "evaluation" / "local-quality-contract-v1.json"
)


def test_local_quality_contract_metrics_pass_but_never_production() -> None:
    payload = evaluate_local_quality_contract(FIXTURE)

    assert payload["version"] == "local-quality-contract-v1"
    assert payload["sample_count"] == 3
    assert payload["local_contract_passed"] is True
    assert payload["real_human_labels"] is False
    assert payload["suitable_for_production_acceptance"] is False
    assert payload["blocked_production_gates"] == list(BLOCKED_PRODUCTION_GATES)

    metrics = payload["metrics"]
    assert metrics["citation_consistency"]["value"] == 1.0
    assert metrics["unsourced_fact_rate"]["value"] == 0.0
    assert metrics["rumor_mislabel_rate"]["value"] == 0.0
    assert metrics["rumor_mislabel_rate"]["denominator"] == 1
