#!/usr/bin/env python3
"""Sample open-classification routing with a configured real LLM provider."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain import Document  # noqa: E402
from app.events.router import EventRouter  # noqa: E402
from app.model_gateway.config import resolve_provider_for_operation  # noqa: E402
from app.model_gateway.secrets import SecretBox  # noqa: E402
from app.model_gateway.service import DeterministicProvider, ModelGateway  # noqa: E402
from app.platform.repository import InMemoryRepository  # noqa: E402

SAMPLES = (
    {
        "title": "某公司签署10亿元重大合同",
        "content": "公司与核心客户签署重大合同，合同金额为人民币10亿元，履行期限24个月。",
    },
    {
        "title": "美联储宣布降息25个基点",
        "content": "美联储在最新议息会议上宣布下调联邦基金利率目标区间25个基点。",
    },
    {
        "title": "某地举办马拉松赛事",
        "content": "本市将于下月举办国际马拉松赛事，预计吸引三万名选手参与。",
    },
)


def _has_real_provider() -> bool:
    repository = InMemoryRepository()
    secrets = SecretBox.from_settings()
    provider = resolve_provider_for_operation(repository, secrets, "event_route")
    return not isinstance(provider, DeterministicProvider)


def main() -> int:
    parser = argparse.ArgumentParser(description="Open classification LLM validation sample")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if os.getenv("FINSIGHT_LLM_VALIDATION_FORCE") != "1" and not _has_real_provider():
        print("LLM validation skipped: no configured non-deterministic provider")
        return 0

    repository = InMemoryRepository()
    router = EventRouter(ModelGateway(repository))
    now = datetime.now(timezone.utc)
    results = []
    for index, sample in enumerate(SAMPLES[: max(args.limit, 1)]):
        document = Document(
            id=f"doc_sample_{index}",
            source_id="validation",
            source_tier="S",
            external_id=f"validation-{index}",
            canonical_url=f"https://example.test/validation/{index}",
            title=sample["title"],
            content=sample["content"],
            content_hash=f"hash-{index}",
            published_at=now,
            ingested_at=now,
        )
        decision = router.route(document)
        results.append(
            {
                "title": sample["title"],
                "rule_hint_type": decision.rule_hint_type,
                "relevance": decision.relevance,
                "event_type": decision.event_type,
                "confidence": decision.confidence,
                "is_candidate_type": decision.is_candidate_type,
                "used_fallback": decision.used_fallback,
                "model_run_id": decision.model_run_id,
            }
        )

    payload = {"status": "completed", "sample_count": len(results), "results": results}
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
