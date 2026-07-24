"""Fail CI when a relative Markdown link points to a missing repository file."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^]]*\]\(([^)]+)\)")


def main() -> int:
    missing: list[str] = []
    for path in ROOT.rglob("*.md"):
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            target = target.split("#", 1)[0].strip("<>")
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).exists():
                missing.append(f"{path.relative_to(ROOT)} -> {target}")
    if missing:
        print("Broken Markdown links:\n" + "\n".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
