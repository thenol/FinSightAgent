from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.analysis.backfill import ImpactProjectionBackfillService
from app.domain import Event, ImpactAnalysis
from app.main import create_app
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository


def _approved_analysis(repository: InMemoryRepository, *, occurred_at: datetime) -> ImpactAnalysis:
    event = Event(
        id=new_id("evt"),
        event_type="macro_policy",
        status="triaged",
        title="政策传导案例",
        entity_ids=[],
        document_ids=[],
        importance=0.9,
        urgency="high",
        occurred_at=occurred_at,
    )
    repository.save_event(event)
    analysis = ImpactAnalysis(
        id=new_id("imp"),
        event_id=event.id,
        version=1,
        status="approved",
        event_title_snapshot=event.title,
        summary="测试回填",
        transmission_chains=[],
        impacts=[
            {
                "target_type": "industry",
                "target_name": "银行",
                "target_code": "cn-banks",
                "direction": "positive",
                "magnitude": "moderate",
                "horizon": "short",
                "confidence": 0.8,
            }
        ],
        macro_assumptions=[],
        watch_items=[],
        generated_by="test",
        created_at=occurred_at,
    )
    repository.save_impact_analysis(analysis)
    return analysis


def test_backfill_projects_approved_analysis_and_is_idempotent_at_cutoff() -> None:
    repository = InMemoryRepository()
    cutoff = datetime.now(timezone.utc)
    _approved_analysis(repository, occurred_at=cutoff - timedelta(hours=1))
    service = ImpactProjectionBackfillService(repository)

    first = service.run(as_of=cutoff)
    second = service.run(as_of=cutoff)

    assert first.approved_analyses == 1
    assert first.contributions_created == 1
    assert first.active_contributions == 1
    assert first.snapshots_created == 1
    assert second.contributions_created == 0
    assert second.snapshots_created == 0


def test_backfill_reports_expired_contributions_without_reactivating_them() -> None:
    repository = InMemoryRepository()
    cutoff = datetime.now(timezone.utc)
    _approved_analysis(repository, occurred_at=cutoff - timedelta(days=30))

    report = ImpactProjectionBackfillService(repository).run(as_of=cutoff)

    assert report.contributions_created == 1
    assert report.expired_contributions == 1
    assert report.active_contributions == 0


def test_backfill_api_is_admin_only_and_requires_timezone_when_supplied() -> None:
    with TestClient(create_app()) as client:
        token = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        ).json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = client.post("/api/v1/impact-projections/backfill", json={}, headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["events_scanned"] == 0
        invalid = client.post(
            "/api/v1/impact-projections/backfill",
            json={"as_of": "2026-08-23T00:00:00"},
            headers=headers,
        )
        assert invalid.status_code == 422
