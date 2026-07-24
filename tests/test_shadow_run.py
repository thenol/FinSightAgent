import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "shadow" / "samples.json"
AS_OF = datetime(2026, 7, 31, tzinfo=timezone.utc)


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shadow_run = _load_script("shadow_run")
mvp_acceptance = _load_script("mvp_acceptance")


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        current = self.value
        self.value += 0.001
        return current


def _run(**overrides):
    arguments = {
        "fixture_path": FIXTURE,
        "as_of": AS_OF,
        "sample_limit": None,
        "seed": 17,
        "clock": FakeClock(),
    }
    arguments.update(overrides)
    return shadow_run.run_shadow(**arguments)


def test_shadow_run_is_deterministic_for_fixed_as_of_limit_and_seed() -> None:
    first = _run(sample_limit=3)
    second = _run(sample_limit=3)

    assert first == second
    assert first["selected_sample_count"] == 3
    assert [event["sample_id"] for event in first["events"]] == [
        event["sample_id"] for event in second["events"]
    ]


def test_shadow_run_excludes_future_data_before_reading_event_fields(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["samples"].append(
        {
            "id": "future-poison",
            "available_at": "2026-08-02T00:00:00+00:00"
        }
    )
    fixture = tmp_path / "samples.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    report = _run(fixture_path=fixture)

    assert report["future_samples_excluded"] == 2
    assert "future-poison" not in {event["sample_id"] for event in report["events"]}


def test_shadow_run_replay_does_not_create_duplicate_reports() -> None:
    report = _run()
    metrics = report["metrics"]

    assert metrics["idempotent_replay_rate"]["value"] == 1.0
    assert metrics["duplicate_report_rate"] == {
        "numerator": 0,
        "denominator": 5,
        "value": 0.0,
    }
    assert all(event["replay_status"] == "duplicate" for event in report["events"])


def test_shadow_run_reports_required_operational_metrics_and_dry_run() -> None:
    report = _run()
    metrics = report["metrics"]

    assert metrics["success_or_explicit_degradation_rate"]["value"] == 1.0
    assert metrics["latency_ms"]["p50"] == 1.0
    assert metrics["latency_ms"]["p95"] == 1.0
    assert metrics["citation_completeness"]["value"] == 1.0
    assert {"model_calls", "tool_calls", "estimated_cost_usd", "human_review_rate"} <= set(
        metrics
    )
    assert all(
        {"model_calls", "tool_calls", "estimated_cost_usd"} <= set(event)
        for event in report["events"]
    )

    dry_run = _run(dry_run=True)
    assert not dry_run["metrics"]["success_or_explicit_degradation_rate"]["denominator"]
    assert all(event["status"] == "planned" for event in dry_run["events"])


def test_acceptance_marks_market_stub_not_production_validated(tmp_path: Path) -> None:
    shadow_path = tmp_path / "shadow.json"
    shadow_path.write_text(json.dumps(_run()), encoding="utf-8")

    report = mvp_acceptance.build_acceptance_report(shadow_result_path=shadow_path)

    market_gate = next(gate for gate in report["gates"] if gate["id"] == "DOC05-MARKET-OUTCOME")
    assert market_gate["status"] == "NOT_PRODUCTION_VALIDATED"
    assert report["market"]["provider"] == "deterministic-market-data-stub"
    assert report["market"]["real_market_data"] is False
    assert report["market"]["suitable_for_real_market_acceptance"] is False
    assert [item["horizon"] for item in report["market"]["horizons"]] == [1, 3, 5, 20]
    assert report["overall_status"] == "NOT_PRODUCTION_VALIDATED"

    contract = report["local_quality_contract"]
    assert contract["suitable_for_production_acceptance"] is False
    assert contract["real_human_labels"] is False
    assert contract["local_contract_passed"] is True
    for gate_id in (
        "DOC05-Q-CITATION-CONSISTENCY",
        "DOC05-Q-UNSOURCED-FACTS",
        "DOC05-Q-RUMOR-MISLABEL",
    ):
        gate = next(item for item in report["gates"] if item["id"] == gate_id)
        assert gate["status"] == "NOT_PRODUCTION_VALIDATED"
        assert gate["source"] == "local-quality-contract-v1"


def test_acceptance_exit_codes_are_nonzero_only_for_failure(tmp_path: Path, capsys) -> None:
    shadow = _run()
    passing_path = tmp_path / "passing.json"
    passing_path.write_text(json.dumps(shadow), encoding="utf-8")
    assert mvp_acceptance.main(["--shadow-result", str(passing_path)]) == 0
    capsys.readouterr()

    shadow["metrics"]["success_or_explicit_degradation_rate"].update(
        {"numerator": 0, "value": 0.0}
    )
    failing_path = tmp_path / "failing.json"
    failing_path.write_text(json.dumps(shadow), encoding="utf-8")
    assert mvp_acceptance.main(["--shadow-result", str(failing_path)]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["overall_status"] == "FAIL"
