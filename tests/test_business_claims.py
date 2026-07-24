import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.application.pipeline import EventResearchPipeline
from app.evidence.claims import ClaimFingerprint, ClaimNormalizer
from app.platform.repository import InMemoryRepository

FIXTURE = Path(__file__).parent / "fixtures" / "business_claims" / "samples.json"


def process_case(case: dict, suffix: str = ""):
    repository = InMemoryRepository()
    result = EventResearchPipeline(repository).process(
        idempotency_key=None,
        source_id="official",
        source_tier="S",
        external_id=f"business-{case['event_type']}{suffix}",
        url="https://example.test/disclosure",
        title=case["title"],
        content=case["content"],
        published_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )
    claims = [claim for claim in repository.claims.values() if claim.event_id == result.event.id]
    return repository, result, claims


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text(encoding="utf-8")))
def test_five_event_types_create_typed_business_claims(case: dict) -> None:
    repository, result, claims = process_case(case)

    assert result.event.event_type == case["event_type"]
    assert {claim.predicate for claim in claims} == set(case["predicates"])
    for claim in claims:
        assert claim.predicate != "document_discloses_event"
        assert claim.object_value["type"]
        assert claim.evidence_ids
        evidence = repository.get_evidence(claim.evidence_ids[0])
        assert evidence is not None
        locator = evidence.locator
        cited_text = result.document.content[locator["char_start"] : locator["char_end"]]
        assert cited_text == evidence.excerpt
        assert repository.list_claim_evidence(claim.id)[0].evidence_id == evidence.id


def test_missing_business_key_field_keeps_needs_review_and_only_supported_claim() -> None:
    case = {
        "event_type": "major_contract",
        "title": "示例公司（000001.SZ）重大合同公告",
        "content": "公司披露重大合同，合同金额为15000万元。",
    }
    _, result, claims = process_case(case)

    assert result.event.status == "needs_review"
    assert "counterparties" in result.event.missing_required
    assert [claim.predicate for claim in claims] == ["signs_major_contract"]


def test_empty_source_text_creates_no_claim() -> None:
    repository = InMemoryRepository()
    with pytest.raises(RuntimeError, match="NO_SOURCE_TEXT"):
        EventResearchPipeline(repository).process(
            idempotency_key=None,
            source_id="official",
            source_tier="S",
            external_id="empty-source",
            url="https://example.test/empty",
            title="示例公司重大合同公告",
            content="",
            published_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        )
    assert not repository.claims


def test_business_claim_fingerprint_changes_with_reduction_ratio() -> None:
    normalizer = ClaimNormalizer()
    first = normalizer.normalize(
        subject_text="000001.SZ",
        subject_entity_id="ent_001",
        predicate="reduces_holding_ratio",
        object_value={"type": "decimal", "value": "5", "unit": "percent"},
        qualifiers={"shareholder": "张三", "stage": "planned"},
        as_of="2026-07-12T00:00:00+00:00",
    )
    changed = normalizer.normalize(
        subject_text="000001.SZ",
        subject_entity_id="ent_001",
        predicate="reduces_holding_ratio",
        object_value={"type": "decimal", "value": "6", "unit": "percent"},
        qualifiers={"shareholder": "张三", "stage": "planned"},
        as_of="2026-07-12T00:00:00+00:00",
    )

    fingerprint = ClaimFingerprint()
    assert fingerprint.compute(first) != fingerprint.compute(changed)
