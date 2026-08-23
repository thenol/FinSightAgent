"""Governed market reference data and impact-target mapping workflow."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timezone

from app.domain import (
    ImpactTargetMapping,
    IndustryClassification,
    IndustryTaxonomy,
    InstrumentIndustryMembership,
    MarketMasterDataImportRun,
)
from app.market.reference import DEFAULT_INSTRUMENTS, MarketInstrumentCatalog
from app.platform.ids import new_id
from app.platform.repository import Repository, RepositoryProvider

DEFAULT_TAXONOMY_ID = "tax:finsight-industry:v1"
DEFAULT_CLASSIFICATIONS = (
    IndustryClassification(
        id="ind:finsight-industry-v1:cn-banks",
        taxonomy_id=DEFAULT_TAXONOMY_ID,
        code="cn-banks",
        name="银行",
        level=1,
        aliases=["银行业", "商业银行"],
    ),
    IndustryClassification(
        id="ind:finsight-industry-v1:cn-real-estate",
        taxonomy_id=DEFAULT_TAXONOMY_ID,
        code="cn-real-estate",
        name="房地产",
        level=1,
        aliases=["房地产业", "地产"],
    ),
)


def seed_market_master_data(repository: Repository) -> MarketInstrumentCatalog:
    """Idempotently seed bootstrap reference data and return the persisted catalog."""

    now = datetime.now(timezone.utc)
    existing_ids = {item.id for item in repository.list_market_instruments()}
    for instrument in DEFAULT_INSTRUMENTS:
        if instrument.id not in existing_ids:
            repository.save_market_instrument(instrument)
    taxonomy_ids = {item.id for item in repository.list_industry_taxonomies()}
    if DEFAULT_TAXONOMY_ID not in taxonomy_ids:
        repository.save_industry_taxonomy(
            IndustryTaxonomy(
                id=DEFAULT_TAXONOMY_ID,
                standard="finsight-industry",
                version="v1",
                name="FinSight 行业分类",
                status="published",
                source="bootstrap",
                effective_from=now,
                created_at=now,
            )
        )
    classification_ids = {
        item.id for item in repository.list_industry_classifications(DEFAULT_TAXONOMY_ID)
    }
    for classification in DEFAULT_CLASSIFICATIONS:
        if classification.id not in classification_ids:
            repository.save_industry_classification(classification)
    memberships = {
        (item.instrument_id, item.taxonomy_id, item.industry_code)
        for item in repository.list_instrument_industry_memberships()
    }
    for instrument in DEFAULT_INSTRUMENTS:
        if not instrument.sector_code:
            continue
        key = (instrument.id, DEFAULT_TAXONOMY_ID, instrument.sector_code)
        if key not in memberships:
            repository.save_instrument_industry_membership(
                InstrumentIndustryMembership(
                    id=f"iim:{instrument.id}:{instrument.sector_code}",
                    instrument_id=instrument.id,
                    taxonomy_id=DEFAULT_TAXONOMY_ID,
                    industry_code=instrument.sector_code,
                    status="approved",
                    source="bootstrap",
                    valid_from=now,
                    created_at=now,
                )
            )
    return MarketInstrumentCatalog(tuple(repository.list_market_instruments(active=True)))


class ImpactTargetMappingService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def suggest(self, *, target_id: str, created_by: str) -> list[ImpactTargetMapping]:
        target = self.repository.get_impact_target(target_id)
        if target is None:
            raise KeyError(target_id)
        target_refs = {_normal(target.canonical_name), *(_normal(x) for x in target.aliases)}
        candidates: list[tuple[str, str, float, str]] = []
        for instrument in self.repository.list_market_instruments(active=True):
            refs = {_normal(instrument.name), _normal(instrument.symbol), _normal(instrument.id)}
            if target_refs & refs:
                candidates.append(("instrument", instrument.id, 1.0, "exact_instrument_reference"))
        for industry in self.repository.list_industry_classifications():
            refs = {_normal(industry.name), *(_normal(x) for x in industry.aliases)}
            if target_refs & refs:
                candidates.append(("industry", industry.code, 0.9, "exact_industry_reference"))
        existing = {
            (item.mapping_type, item.mapping_code)
            for item in self.repository.list_impact_target_mappings(target_id)
        }
        now = datetime.now(timezone.utc)
        created: list[ImpactTargetMapping] = []
        for mapping_type, mapping_code, confidence, reason in candidates:
            if (mapping_type, mapping_code) in existing:
                continue
            value = ImpactTargetMapping(
                id=new_id("itm"),
                target_id=target_id,
                mapping_type=mapping_type,
                mapping_code=mapping_code,
                weight=1.0,
                confidence=confidence,
                status="proposed",
                reason=reason,
                source="exact_reference_suggestion_v1",
                created_by=created_by,
                created_at=now,
            )
            self.repository.save_impact_target_mapping(value)
            created.append(value)
        return created

    def transition(self, mapping_id: str, *, status: str, reviewed_by: str) -> ImpactTargetMapping:
        value = self.repository.get_impact_target_mapping(mapping_id)
        if value is None:
            raise KeyError(mapping_id)
        allowed = {"proposed": {"approved", "rejected"}, "approved": {"retired"}}
        if status not in allowed.get(value.status, set()):
            raise ValueError("IMPACT_TARGET_MAPPING_TRANSITION_INVALID")
        updated = replace(
            value,
            status=status,
            reviewed_by=reviewed_by,
            reviewed_at=datetime.now(timezone.utc),
        )
        self.repository.update_impact_target_mapping(updated)
        return updated


class MarketMasterDataImportService:
    """Validate, stage and publish a complete industry taxonomy snapshot."""

    def __init__(self, repository: RepositoryProvider) -> None:
        self.repository = repository

    def stage(
        self,
        *,
        standard: str,
        version: str,
        name: str,
        source: str,
        effective_from: datetime,
        classifications: list[dict],
        memberships: list[dict],
        source_metadata: dict,
        created_by: str,
    ) -> MarketMasterDataImportRun:
        canonical = {
            "standard": standard,
            "version": version,
            "name": name,
            "source": source,
            "effective_from": effective_from.isoformat(),
            "classifications": sorted(classifications, key=lambda item: item["code"]),
            "memberships": sorted(
                memberships,
                key=lambda item: (item["instrument_id"], item["industry_code"]),
            ),
            "source_metadata": source_metadata,
        }
        source_hash = hashlib.sha256(
            json.dumps(
                canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        existing = self.repository.find_market_master_data_import_run_by_hash(source_hash)
        if existing is not None:
            return existing
        errors, warnings = self._validate(
            standard=standard,
            version=version,
            effective_from=effective_from,
            classifications=classifications,
            memberships=memberships,
        )
        now = datetime.now(timezone.utc)
        run = MarketMasterDataImportRun(
            id=new_id("mdi"),
            standard=standard,
            version=version,
            source=source,
            source_hash=source_hash,
            status="rejected" if errors else "validated",
            classification_count=len(classifications),
            membership_count=len(memberships),
            errors=errors,
            warnings=warnings,
            source_metadata=source_metadata,
            created_by=created_by,
            created_at=now,
        )
        if errors:
            self.repository.save_market_master_data_import_run(run)
            return run
        taxonomy_id = f"tax:{standard}:{version}"
        with self.repository.transaction() as repository:
            repository.save_market_master_data_import_run(run)
            repository.save_industry_taxonomy(
                IndustryTaxonomy(
                    id=taxonomy_id,
                    standard=standard,
                    version=version,
                    name=name,
                    status="draft",
                    source=source,
                    effective_from=effective_from,
                    created_at=now,
                )
            )
            for item in classifications:
                repository.save_industry_classification(
                    IndustryClassification(
                        id=f"ind:{taxonomy_id}:{item['code']}",
                        taxonomy_id=taxonomy_id,
                        code=item["code"],
                        name=item["name"],
                        level=item["level"],
                        parent_code=item.get("parent_code"),
                        aliases=item.get("aliases", []),
                        status="active",
                        valid_from=effective_from,
                    )
                )
            for item in memberships:
                repository.save_instrument_industry_membership(
                    InstrumentIndustryMembership(
                        id=(f"iim:{item['instrument_id']}:{taxonomy_id}:{item['industry_code']}"),
                        instrument_id=item["instrument_id"],
                        taxonomy_id=taxonomy_id,
                        industry_code=item["industry_code"],
                        weight=item.get("weight", 1.0),
                        is_primary=item.get("is_primary", True),
                        status="proposed",
                        source=source,
                        valid_from=effective_from,
                        created_at=now,
                    )
                )
        return run

    def publish(self, run_id: str) -> MarketMasterDataImportRun:
        run = self.repository.get_market_master_data_import_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status == "published":
            return run
        if run.status != "validated":
            raise ValueError("MARKET_MASTER_IMPORT_NOT_PUBLISHABLE")
        taxonomy_id = f"tax:{run.standard}:{run.version}"
        taxonomies = self.repository.list_industry_taxonomies()
        taxonomy = next((item for item in taxonomies if item.id == taxonomy_id), None)
        if taxonomy is None:
            raise ValueError("MARKET_MASTER_IMPORT_TAXONOMY_MISSING")
        now = datetime.now(timezone.utc)
        cutover_at = taxonomy.effective_from or now
        with self.repository.transaction() as repository:
            for item in taxonomies:
                if (
                    item.standard == run.standard
                    and item.status == "published"
                    and item.id != taxonomy_id
                ):
                    repository.save_industry_taxonomy(
                        replace(item, status="retired", effective_to=cutover_at)
                    )
            repository.save_industry_taxonomy(replace(taxonomy, status="published"))
            for membership in repository.list_instrument_industry_memberships():
                if membership.taxonomy_id == taxonomy_id:
                    repository.save_instrument_industry_membership(
                        replace(membership, status="approved")
                    )
                elif membership.status == "approved" and any(
                    item.id == membership.taxonomy_id and item.standard == run.standard
                    for item in taxonomies
                ):
                    repository.save_instrument_industry_membership(
                        replace(membership, valid_to=cutover_at)
                    )
            published = replace(run, status="published", published_at=now)
            repository.update_market_master_data_import_run(published)
        return published

    def _validate(
        self,
        *,
        standard: str,
        version: str,
        effective_from: datetime,
        classifications: list[dict],
        memberships: list[dict],
    ) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        if effective_from.tzinfo is None:
            errors.append("effective_from_timezone_required")
        if not standard.strip() or not version.strip():
            errors.append("standard_and_version_required")
        codes = [item["code"] for item in classifications]
        if len(codes) != len(set(codes)):
            errors.append("duplicate_industry_code")
        by_code = {item["code"]: item for item in classifications}
        for item in classifications:
            parent = item.get("parent_code")
            if parent and parent not in by_code:
                errors.append(f"parent_not_found:{item['code']}:{parent}")
            elif parent and by_code[parent]["level"] >= item["level"]:
                errors.append(f"parent_level_invalid:{item['code']}:{parent}")
        instrument_ids = {item.id for item in self.repository.list_market_instruments(active=True)}
        seen_memberships: set[tuple[str, str]] = set()
        weights: dict[str, float] = {}
        primaries: dict[str, int] = {}
        for item in memberships:
            key = (item["instrument_id"], item["industry_code"])
            if key in seen_memberships:
                errors.append(f"duplicate_membership:{key[0]}:{key[1]}")
            seen_memberships.add(key)
            if item["instrument_id"] not in instrument_ids:
                errors.append(f"instrument_not_found:{item['instrument_id']}")
            if item["industry_code"] not in by_code:
                errors.append(f"industry_not_found:{item['industry_code']}")
            weight = float(item.get("weight", 1.0))
            if not 0 < weight <= 1:
                errors.append(f"membership_weight_invalid:{key[0]}:{key[1]}")
            weights[key[0]] = weights.get(key[0], 0.0) + weight
            if item.get("is_primary", True):
                primaries[key[0]] = primaries.get(key[0], 0) + 1
        errors.extend(
            f"membership_weight_sum_exceeds_one:{key}"
            for key, value in weights.items()
            if value > 1.000001
        )
        errors.extend(
            f"multiple_primary_memberships:{key}" for key, value in primaries.items() if value > 1
        )
        if not memberships:
            warnings.append("taxonomy_has_no_memberships")
        existing = [
            item
            for item in self.repository.list_industry_taxonomies()
            if item.standard == standard and item.version == version
        ]
        if existing:
            errors.append("taxonomy_version_already_exists")
        return sorted(set(errors)), warnings


def _normal(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()
