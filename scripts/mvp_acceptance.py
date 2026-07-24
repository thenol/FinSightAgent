#!/usr/bin/env python3
"""Build a machine-readable MVP acceptance report from local evaluation artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.evaluation.assessor import Assessor  # noqa: E402
from app.evaluation.local_quality_contract import (  # noqa: E402
    evaluate_local_quality_contract,
)
from app.evaluation.market import (  # noqa: E402
    acceptance_market_payload,
    build_acceptance_market_stub,
)
from app.platform.repository import InMemoryRepository  # noqa: E402

VERSION = "mvp-acceptance-v1"
PASS = "PASS"
FAIL = "FAIL"
NOT_VALIDATED = "NOT_PRODUCTION_VALIDATED"


def _gate(
    gate_id: str,
    value: float,
    threshold: float,
    direction: str,
    *,
    sample_size: int,
    confidence_interval: Sequence[float] | None = None,
    source: str,
) -> dict[str, Any]:
    if direction == "min":
        passed = value >= threshold
    elif direction == "max_exclusive":
        passed = value < threshold
    else:
        passed = value <= threshold
    return {
        "id": gate_id,
        "status": PASS if passed else FAIL,
        "value": value,
        "threshold": threshold,
        "direction": direction,
        "sample_size": sample_size,
        "confidence_interval_95": list(confidence_interval or []),
        "source": source,
    }


def _not_validated(gate_id: str, reason: str, source: str) -> dict[str, Any]:
    return {
        "id": gate_id,
        "status": NOT_VALIDATED,
        "reason": reason,
        "source": source,
    }


def _market_stub_summary(as_of: datetime) -> dict[str, Any]:
    return acceptance_market_payload(build_acceptance_market_stub(as_of))


def build_acceptance_report(
    *,
    shadow_result_path: Path,
    assessor_fixture_path: Path | None = None,
    local_quality_fixture_path: Path | None = None,
) -> dict[str, Any]:
    shadow = json.loads(shadow_result_path.read_text(encoding="utf-8"))
    as_of = datetime.fromisoformat(str(shadow["as_of"]).replace("Z", "+00:00"))
    if as_of.tzinfo is None:
        raise ValueError("shadow as_of must include a timezone")
    assessor = Assessor(InMemoryRepository(), assessor_fixture_path).evaluate(now=as_of)
    assessor_metrics = {metric.metric: metric for metric in assessor.metrics}
    shadow_metrics: Mapping[str, Any] = shadow["metrics"]
    local_quality = evaluate_local_quality_contract(local_quality_fixture_path)

    gates: list[dict[str, Any]] = []
    for name, gate_id in (
        ("classification_accuracy", "DOC05-Q-CLASSIFICATION"),
        ("entity_alignment_accuracy", "DOC05-Q-ENTITY-ALIGNMENT"),
        ("citation_completeness", "DOC05-Q-ASSESSOR-CITATION"),
    ):
        metric = assessor_metrics[name]
        gates.append(
            _gate(
                gate_id,
                metric.rate,
                metric.threshold,
                "min",
                sample_size=metric.total,
                confidence_interval=(metric.lower_bound, metric.upper_bound),
                source="Assessor",
            )
        )

    workflow = shadow_metrics["success_or_explicit_degradation_rate"]
    gates.append(
        _gate(
            "DOC05-Q-WORKFLOW-COMPLETION",
            float(workflow["value"]),
            0.98,
            "min",
            sample_size=int(workflow["denominator"]),
            source="shadow_run",
        )
        if workflow["denominator"]
        else _not_validated(
            "DOC05-Q-WORKFLOW-COMPLETION", "no executed shadow samples", "shadow_run"
        )
    )
    citations = shadow_metrics["citation_completeness"]
    gates.append(
        _gate(
            "DOC05-Q-CITATION-COMPLETENESS",
            float(citations["value"]),
            1.0,
            "min",
            sample_size=int(citations["denominator"]),
            source="shadow_run",
        )
        if citations["denominator"]
        else _not_validated(
            "DOC05-Q-CITATION-COMPLETENESS", "no critical claims", "shadow_run"
        )
    )
    duplicates = shadow_metrics["duplicate_report_rate"]
    gates.append(
        _gate(
            "DOC05-Q-DUPLICATE-REPORT",
            float(duplicates["value"]),
            0.01,
            "max_exclusive",
            sample_size=int(duplicates["denominator"]),
            source="shadow_run",
        )
        if duplicates["denominator"]
        else _not_validated("DOC05-Q-DUPLICATE-REPORT", "no reports", "shadow_run")
    )
    # Docs/05 requires production human labels. Local contract metrics are evidence-only.
    local_source = local_quality["version"]
    gates.extend(
        [
            _not_validated(
                "DOC05-Q-CITATION-CONSISTENCY",
                "local quality contract is deterministic and not production human labels",
                local_source,
            ),
            _not_validated(
                "DOC05-Q-UNSOURCED-FACTS",
                "local quality contract is deterministic and not a frozen factuality set",
                local_source,
            ),
            _not_validated(
                "DOC05-Q-RUMOR-MISLABEL",
                "local quality contract is deterministic and not production rumor labels",
                local_source,
            ),
            _not_validated(
                "DOC05-NFR-LATENCY",
                "docs/05 defers the production SLA until a real-data load baseline exists",
                "shadow_run",
            ),
            _not_validated(
                "DOC05-NFR-COST",
                "local deterministic services make no billable model or external tool calls",
                "shadow_run",
            ),
        ]
    )

    market = _market_stub_summary(as_of)
    gates.append(
        _not_validated(
            "DOC05-MARKET-OUTCOME",
            "deterministic market Stub is contract-only and is not real market data",
            market["provider"],
        )
    )
    statuses = Counter(gate["status"] for gate in gates)
    overall_status = FAIL if statuses[FAIL] else (
        NOT_VALIDATED if statuses[NOT_VALIDATED] else PASS
    )
    return {
        "version": VERSION,
        "as_of": as_of.isoformat(),
        "overall_status": overall_status,
        "status_counts": dict(sorted(statuses.items())),
        "assessor": {
            "version": assessor.version,
            "sample_count": assessor.sample_count,
            "event_distribution": assessor.event_distribution,
        },
        "shadow": {
            "version": shadow.get("version"),
            "selected_sample_count": shadow.get("selected_sample_count"),
            "event_distribution": shadow.get("event_distribution"),
            "metrics": shadow_metrics,
        },
        "market": market,
        "local_quality_contract": local_quality,
        "gates": gates,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow-result", required=True, type=Path)
    parser.add_argument("--assessor-fixture", type=Path)
    parser.add_argument("--local-quality-fixture", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_acceptance_report(
            shadow_result_path=args.shadow_result,
            assessor_fixture_path=args.assessor_fixture,
            local_quality_fixture_path=args.local_quality_fixture,
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"overall_status": FAIL, "error": str(exc)}, ensure_ascii=False))
        return 2
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if report["overall_status"] == FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
