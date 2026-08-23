#!/usr/bin/env python3
"""Optional HTTP smoke test against a running FinSight API stack."""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from json import dumps, loads


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict | None = None,
):
    data = dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read().decode("utf-8")
        return response.status, loads(payload) if payload else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="FinSight API smoke test")
    parser.add_argument(
        "--base-url",
        default=os.getenv("FINSIGHT_E2E_BASE_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument("--username", default=os.getenv("FINSIGHT_E2E_USERNAME", "admin"))
    parser.add_argument("--password", default=os.getenv("FINSIGHT_E2E_PASSWORD", "admin123"))
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    try:
        ready_status, ready = _request("GET", f"{base}/health/ready")
        assert ready_status == 200, ready
        assert ready.get("status") == "ready", ready

        login_status, login = _request(
            "POST",
            f"{base}/api/v1/auth/login",
            body={"username": args.username, "password": args.password},
        )
        assert login_status == 200, login
        token = login["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "e2e-smoke"}

        ingest_status, ingested = _request(
            "POST",
            f"{base}/api/v1/documents/ingest",
            headers=headers,
            body={
                "source_id": "sse",
                "source_tier": "S",
                "external_id": "e2e-smoke",
                "url": "https://example.test/e2e-smoke",
                "title": "示例公司（600000.SH）重大合同公告",
                "content": "公司与客户签署重大合同，合同金额为人民币1亿元。",
                "published_at": "2026-07-12T09:30:00+08:00",
            },
        )
        assert ingest_status == 201, ingested
        event_id = ingested["data"]["event_id"]

        detail_status, detail = _request(
            "GET",
            f"{base}/api/v1/events/{event_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert detail_status == 200, detail
        assert detail["data"]["event_type"] == "major_contract"
    except (AssertionError, urllib.error.URLError, TimeoutError) as exc:
        print(f"E2E smoke failed: {exc}", file=sys.stderr)
        return 1

    print("E2E smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
