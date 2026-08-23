from datetime import datetime, timezone

from app.market.master_data import MarketMasterDataImportService, seed_market_master_data
from app.platform.repository import InMemoryRepository


def _payload() -> dict:
    return {
        "standard": "finsight-industry",
        "version": "v2",
        "name": "FinSight 行业分类 v2",
        "source": "licensed-reference-feed",
        "effective_from": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "classifications": [
            {
                "code": "cn-financials",
                "name": "金融",
                "level": 1,
                "parent_code": None,
                "aliases": [],
            },
            {
                "code": "cn-banks",
                "name": "银行",
                "level": 2,
                "parent_code": "cn-financials",
                "aliases": ["银行业"],
            },
        ],
        "memberships": [
            {
                "instrument_id": "cn:etf:512800",
                "industry_code": "cn-banks",
                "weight": 1.0,
                "is_primary": True,
            }
        ],
        "source_metadata": {"license": "internal-test", "snapshot": "2026-08-23"},
        "created_by": "admin",
    }


def test_market_master_import_is_staged_then_atomically_published() -> None:
    repository = InMemoryRepository()
    seed_market_master_data(repository)
    service = MarketMasterDataImportService(repository)

    staged = service.stage(**_payload())

    assert staged.status == "validated"
    assert repository.list_industry_taxonomies(status="draft")[0].version == "v2"
    proposed = repository.list_instrument_industry_memberships(status="proposed")
    assert len(proposed) == 1
    assert proposed[0].taxonomy_id == "tax:finsight-industry:v2"

    published = service.publish(staged.id)

    assert published.status == "published"
    taxonomies = repository.list_industry_taxonomies()
    assert next(item for item in taxonomies if item.version == "v1").status == "retired"
    assert next(item for item in taxonomies if item.version == "v2").status == "published"
    approved = repository.list_instrument_industry_memberships(status="approved")
    assert len(approved) == 3
    old_bank = next(
        item
        for item in approved
        if item.taxonomy_id == "tax:finsight-industry:v1" and item.industry_code == "cn-banks"
    )
    new_bank = next(item for item in approved if item.taxonomy_id == "tax:finsight-industry:v2")
    assert old_bank.valid_to == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert new_bank.valid_from == old_bank.valid_to


def test_market_master_import_rejects_invalid_references_without_staging() -> None:
    repository = InMemoryRepository()
    seed_market_master_data(repository)
    service = MarketMasterDataImportService(repository)
    payload = _payload()
    payload["version"] = "invalid-v2"
    payload["memberships"][0]["instrument_id"] = "cn:stock:not-found"

    rejected = service.stage(**payload)

    assert rejected.status == "rejected"
    assert "instrument_not_found:cn:stock:not-found" in rejected.errors
    assert not any(item.version == "invalid-v2" for item in repository.list_industry_taxonomies())


def test_market_master_import_reuses_identical_source_snapshot() -> None:
    repository = InMemoryRepository()
    seed_market_master_data(repository)
    service = MarketMasterDataImportService(repository)

    first = service.stage(**_payload())
    second = service.stage(**_payload())

    assert first.id == second.id
    assert len(repository.list_market_master_data_import_runs()) == 1
