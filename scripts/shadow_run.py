#!/usr/bin/env python3
"""Run deterministic, offline shadow samples through the public pipeline service."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.application.pipeline import EventResearchPipeline  # noqa: E402
from app.ingestion.artifacts import InMemoryArtifactStore  # noqa: E402
from app.platform.repository import InMemoryRepository  # noqa: E402

VERSION = "shadow-run-v1"
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "shadow" / "samples.json"
Clock = Callable[[], float]


def _instant(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = rank - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def _rate(numerator: int, denominator: int) -> dict[str, int | float]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": round(numerator / denominator, 6) if denominator else 0.0,
    }


def _fingerprint(event_type: str, title: str, claim_fingerprint: str) -> str:
    value = f"{event_type}\0{title}\0{claim_fingerprint}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _eligible_samples(
    payload: Mapping[str, Any], as_of: datetime, limit: int | None, seed: int
) -> tuple[list[Mapping[str, Any]], int]:
    eligible: list[Mapping[str, Any]] = []
    future_excluded = 0
    for raw in payload.get("samples", []):
        # Check availability before touching any event content.
        available_at = _instant(str(raw["available_at"]), "samples[].available_at")
        if available_at > as_of:
            future_excluded += 1
            continue
        published_at = _instant(str(raw["published_at"]), "samples[].published_at")
        if published_at > as_of:
            raise ValueError(f"sample {raw.get('id', '<unknown>')} published after as_of")
        eligible.append(raw)
    eligible.sort(key=lambda item: str(item["id"]))
    random.Random(seed).shuffle(eligible)
    if limit is not None:
        eligible = eligible[:limit]
    return eligible, future_excluded


def run_shadow(
    *,
    fixture_path: Path = DEFAULT_FIXTURE,
    as_of: datetime,
    sample_limit: int | None = None,
    seed: int = 0,
    dry_run: bool = False,
    clock: Clock = time.perf_counter,
) -> dict[str, Any]:
    """Execute an isolated shadow run and return a JSON-compatible report."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    if sample_limit is not None and sample_limit < 1:
        raise ValueError("sample_limit must be positive")

    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    samples, future_excluded = _eligible_samples(payload, as_of, sample_limit, seed)
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository, InMemoryArtifactStore())
    events: list[dict[str, Any]] = []

    for sample in samples:
        sample_id = str(sample["id"])
        if dry_run:
            events.append(
                {
                    "sample_id": sample_id,
                    "status": "planned",
                    "explicitly_degraded": False,
                    "latency_ms": 0.0,
                    "model_calls": 0,
                    "tool_calls": 0,
                    "estimated_cost_usd": 0.0,
                    "requires_human_review": False,
                    "critical_claims": 0,
                    "cited_critical_claims": 0,
                    "report_fingerprint": None,
                    "replay_status": "not_run",
                }
            )
            continue

        started = clock()
        try:
            result = pipeline.process(
                idempotency_key=f"shadow:{sample_id}",
                source_id=str(sample.get("source_id", "shadow-official")),
                source_tier=str(sample.get("source_tier", "S")),
                external_id=sample_id,
                url=None,
                title=str(sample["title"]),
                content=str(sample["content"]),
                published_at=_instant(str(sample["published_at"]), "published_at"),
            )
            latency_ms = round((clock() - started) * 1000, 3)
            replay = pipeline.process(
                idempotency_key=f"shadow:{sample_id}",
                source_id=str(sample.get("source_id", "shadow-official")),
                source_tier=str(sample.get("source_tier", "S")),
                external_id=sample_id,
                url=None,
                title=str(sample["title"]),
                content=str(sample["content"]),
                published_at=_instant(str(sample["published_at"]), "published_at"),
            )
            # These claims are outputs created from the already cutoff-filtered sample, not
            # additional source observations. Their persistence timestamp is the replay time.
            claims = repository.get_claims_for_event(result.event.id)
            cited = sum(bool(claim.evidence_ids) for claim in claims)
            events.append(
                {
                    "sample_id": sample_id,
                    "status": "succeeded",
                    "explicitly_degraded": False,
                    "latency_ms": latency_ms,
                    "model_calls": 0,
                    "tool_calls": 0,
                    "estimated_cost_usd": 0.0,
                    "requires_human_review": bool(result.event.missing_required),
                    "critical_claims": len(claims),
                    "cited_critical_claims": cited,
                    "report_fingerprint": _fingerprint(
                        result.event.event_type,
                        result.fact_card.title,
                        result.claim.fingerprint,
                    ),
                    "replay_status": replay.status,
                }
            )
        except Exception as exc:  # report per-sample degradation instead of aborting the run
            latency_ms = round((clock() - started) * 1000, 3)
            events.append(
                {
                    "sample_id": sample_id,
                    "status": "degraded",
                    "explicitly_degraded": True,
                    "error": type(exc).__name__,
                    "latency_ms": latency_ms,
                    "model_calls": 0,
                    "tool_calls": 0,
                    "estimated_cost_usd": 0.0,
                    "requires_human_review": True,
                    "critical_claims": 0,
                    "cited_critical_claims": 0,
                    "report_fingerprint": None,
                    "replay_status": "not_run",
                }
            )

    executed = [event for event in events if event["status"] != "planned"]
    completed = sum(
        event["status"] == "succeeded" or event["explicitly_degraded"] for event in executed
    )
    latencies = [float(event["latency_ms"]) for event in executed]
    critical = sum(int(event["critical_claims"]) for event in executed)
    cited = sum(int(event["cited_critical_claims"]) for event in executed)
    fingerprints = [
        str(event["report_fingerprint"]) for event in executed if event["report_fingerprint"]
    ]
    duplicate_count = len(fingerprints) - len(set(fingerprints))
    replay_duplicates = sum(event["replay_status"] == "duplicate" for event in executed)

    return {
        "version": VERSION,
        "fixture_version": str(payload.get("version", "unknown")),
        "as_of": as_of.isoformat(),
        "seed": seed,
        "sample_limit": sample_limit,
        "dry_run": dry_run,
        "selected_sample_count": len(samples),
        "future_samples_excluded": future_excluded,
        "event_distribution": dict(
            sorted(Counter(str(sample["event_type"]) for sample in samples).items())
        ),
        "metrics": {
            "success_or_explicit_degradation_rate": _rate(completed, len(executed)),
            "latency_ms": {
                "values": latencies,
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
            },
            "model_calls": sum(int(event["model_calls"]) for event in executed),
            "tool_calls": sum(int(event["tool_calls"]) for event in executed),
            "estimated_cost_usd": round(
                sum(float(event["estimated_cost_usd"]) for event in executed), 6
            ),
            "human_review_rate": _rate(
                sum(bool(event["requires_human_review"]) for event in executed), len(executed)
            ),
            "citation_completeness": _rate(cited, critical),
            "duplicate_report_rate": _rate(duplicate_count, len(fingerprints)),
            "idempotent_replay_rate": _rate(replay_duplicates, len(executed)),
        },
        "events": events,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--as-of", required=True, help="timezone-aware ISO-8601 cutoff")
    parser.add_argument("--sample-limit", type=int)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, help="also write the JSON report to this path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_shadow(
            fixture_path=args.fixture,
            as_of=_instant(args.as_of, "as_of"),
            sample_limit=args.sample_limit,
            seed=args.seed,
            dry_run=args.dry_run,
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 2
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
