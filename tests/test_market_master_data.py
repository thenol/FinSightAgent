from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.domain import ImpactTargetDefinition
from app.main import create_app
from app.market.factors import EventImpactFactorService
from app.market.master_data import ImpactTargetMappingService, seed_market_master_data
from app.platform.repository import InMemoryRepository


def test_suggested_mapping_is_not_consumed_until_approved() -> None:
    repository = InMemoryRepository()
    catalog = seed_market_master_data(repository)
    target = ImpactTargetDefinition(
        id="target:banking",
        target_type="industry",
        target_code="research:banking",
        canonical_name="银行业",
    )
    repository.save_impact_target(target)
    service = ImpactTargetMappingService(repository)
    suggestions = service.suggest(target_id=target.id, created_by="researcher")

    assert len(suggestions) == 1
    assert suggestions[0].mapping_code == "cn-banks"
    instrument = catalog.get("cn:etf:512800")
    assert instrument is not None
    as_of = datetime.now(timezone.utc)
    before = EventImpactFactorService(repository).snapshot(instrument, as_of=as_of, horizon=3)
    assert before.reason == "impact_target_not_mapped"

    service.transition(suggestions[0].id, status="approved", reviewed_by="reviewer")
    historical = EventImpactFactorService(repository).snapshot(instrument, as_of=as_of, horizon=3)
    assert historical.reason == "impact_target_not_mapped"
    approved_as_of = datetime.now(timezone.utc)
    after = EventImpactFactorService(repository).snapshot(
        instrument, as_of=approved_as_of, horizon=3
    )
    assert after.reason == "approved_impact_snapshot_missing"


def test_mapping_governance_api_lists_and_approves_suggestion() -> None:
    app = create_app()
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        )
        token = login.json()["data"]["access_token"]
        app.state.repository.save_impact_target(
            ImpactTargetDefinition(
                id="target:real-estate",
                target_type="industry",
                target_code="research:real-estate",
                canonical_name="地产",
            )
        )
        headers = {"Authorization": f"Bearer {token}"}
        suggested = client.post(
            "/api/v1/market/impact-target-mappings/suggest",
            json={"target_id": "target:real-estate"},
            headers=headers,
        )
        assert suggested.status_code == 200
        mapping = suggested.json()["data"][0]
        assert mapping["status"] == "proposed"
        approved = client.post(
            f"/api/v1/market/impact-target-mappings/{mapping['id']}/transition",
            json={"status": "approved", "reason": "分类和标的成员关系已复核"},
            headers=headers,
        )
        assert approved.status_code == 200
        assert approved.json()["data"]["status"] == "approved"
        listed = client.get(
            "/api/v1/market/impact-target-mappings",
            params={"status": "approved"},
            headers=headers,
        )
        assert listed.status_code == 200
        assert any(item["id"] == mapping["id"] for item in listed.json()["data"])
