#!/usr/bin/env python3
"""Read-only cross-domain orphan reference audit (IMP-010). Does not delete data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.platform.orphan_audit import (  # noqa: E402
    audit_repository,
    build_repository_from_settings,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON report (default: stdout)",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit 1 when any orphan reference is found",
    )
    args = parser.parse_args()

    repository = build_repository_from_settings()
    report = audit_repository(repository)
    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)

    if args.fail_on_findings and report.finding_count:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
