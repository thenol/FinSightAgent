"""管理后台运营指标 API 测试。"""

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.auth import PASSWORD_HASH
from app.domain import Claim, ModelRun, QuarantineItem, ReviewTask, Source, User, WorkflowRun
from app.main import create_app
from app.platform.ids import new_id


@contextmanager
def _admin_client():
    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        admin = User(
            id=new_id("usr"),
            username="metrics-admin",
            password_hash=PASSWORD_HASH.hash("secret"),
            role="admin",
            status="active",
        )
        repository.save_user(admin)
        login = client.post(
            "/api/v1/auth/login", json={"username": "metrics-admin", "password": "secret"}
        )
        token = login.json()["data"]["access_token"]
        yield client, repository, token


def _seed_workflows(repository, event_id: str) -> None:
    for status in ("succeeded", "succeeded", "failed", "pending"):
        run = WorkflowRun(
            id=new_id("wfr"),
            event_id=event_id,
            trigger_id="manual",
            status=status,
            as_of=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        repository.save_workflow_run(run)


def _seed_model_runs(repository) -> None:
    for idx, status in enumerate(("success", "success", "error")):
        run = ModelRun(
            id=new_id("mdr"),
            operation="synthesize",
            provider="deterministic",
            model="stub",
            input_schema_version="v1",
            output_schema_version="v1",
            request_hash=f"hash-{idx}",
            input_payload={},
            output_payload={},
            status=status,
            latency_ms=100 + idx * 50,
            estimated_cost_usd=0.001 + idx * 0.001,
            created_at=datetime.now(timezone.utc),
        )
        repository.save_model_run(run)


def _seed_reviews(repository) -> None:
    for status, decided in (("pending", None), ("pending", None), ("approved", "approve")):
        task = ReviewTask(
            id=new_id("rev"),
            object_type="report",
            object_id=new_id("rep"),
            reason_code="budget_exceeded",
            allowed_decisions=["approve", "return"],
            status=status,
            decision=decided,
            reviewer_id=new_id("usr") if decided else None,
            comment="ok" if decided else None,
            created_at=datetime.now(timezone.utc),
            decided_at=datetime.now(timezone.utc) if decided else None,
        )
        repository.save_review_task(task)


def _seed_quarantine(repository, source_id: str) -> None:
    repository.save_quarantine_item(
        QuarantineItem(
            id=new_id("qtn"),
            source_id=source_id,
            external_id="ext-1",
            url="https://example.test/a",
            error_code="FETCH_ERROR",
            detail="x",
            status="open",
            created_at=datetime.now(timezone.utc),
        )
    )


def _seed_claims(repository, event_id: str) -> tuple[str, str]:
    empty_claim = Claim(
        id=new_id("clm"),
        event_id=event_id,
        subject_text="empty claim",
        predicate="is",
        object_value={"value": "x"},
        fingerprint="fp-empty",
        status="verified",
        confidence=Decimal("0.9"),
        evidence_ids=[],
        as_of=datetime.now(timezone.utc),
    )
    evidence_claim = Claim(
        id=new_id("clm"),
        event_id=event_id,
        subject_text="evidenced claim",
        predicate="is",
        object_value={"value": "y"},
        fingerprint="fp-evidenced",
        status="verified",
        confidence=Decimal("0.9"),
        evidence_ids=[new_id("evd")],
        as_of=datetime.now(timezone.utc),
    )
    repository.save_claim(empty_claim)
    repository.save_claim(evidence_claim)
    return empty_claim.id, evidence_claim.id


def test_admin_metrics_aggregation() -> None:
    with _admin_client() as (client, repository, token):
        source = Source(
            id=new_id("src"),
            code="test-src",
            name="Test",
            trust_tier="A",
            feed_url="https://example.test/feed",
            allowed_domains=("example.test",),
            adapter_type="rss",
            status="active",
        )
        repository.save_source(source)
        _seed_quarantine(repository, source.id)
        _seed_workflows(repository, "evt_no_event")
        _seed_model_runs(repository)
        _seed_reviews(repository)
        _seed_claims(repository, "evt_no_event")

        response = client.get(
            "/api/v1/admin/metrics", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["workflows"]["total"] == 4
        assert data["workflows"]["by_status"]["succeeded"] == 2
        assert data["workflows"]["by_status"]["failed"] == 1
        assert data["workflows"]["success_rate"] == 2 / 3
        assert data["models"]["total_runs"] == 3
        assert data["models"]["failures"] == 1
        assert data["sources"]["total"] == 1
        assert data["sources"]["open_quarantine"] == 1
        assert data["reviews"]["pending"] == 2
        assert data["reviews"]["decided"] == 1
        assert data["reviews"]["manual_review_rate"] == 1 / 3
        assert data["users"]["total"] >= 1
        assert data["citations"]["total_claims"] == 2
        assert data["citations"]["claims_with_evidence"] == 1
        assert data["citations"]["completeness_rate"] == 0.5


def test_admin_metrics_requires_admin() -> None:
    with TestClient(create_app()) as client:
        researcher = User(
            id=new_id("usr"),
            username="metrics-researcher",
            password_hash=PASSWORD_HASH.hash("secret"),
            role="researcher",
            status="active",
        )
        client.app.state.repository.save_user(researcher)
        login = client.post(
            "/api/v1/auth/login", json={"username": "metrics-researcher", "password": "secret"}
        )
        token = login.json()["data"]["access_token"]
        response = client.get(
            "/api/v1/admin/metrics", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403
