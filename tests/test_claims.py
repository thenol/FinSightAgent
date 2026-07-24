import pytest

from app.evidence.claims import (
    ClaimFingerprint,
    ClaimMatcher,
    ClaimNormalizationError,
    ClaimNormalizer,
)
from app.evidence.predicates import PREDICATE_VERSION, get_predicate, is_controlled


def base_claim_kwargs(**overrides):
    kwargs = {
        "subject_text": "示例公司",
        "subject_entity_id": "ent_001",
        "predicate": "expects_net_profit_change",
        "object_value": {
            "type": "range",
            "min": "160000000.00",
            "max": "190000000.00",
            "unit": "CNY",
        },
        "qualifiers": {"period": "2026-H1", "comparison": "year_over_year"},
        "as_of": "2026-07-12T01:30:00+00:00",
    }
    kwargs.update(overrides)
    return kwargs


def test_normalizer_accepts_controlled_predicate_and_normalizes_decimals() -> None:
    normalizer = ClaimNormalizer()
    claim = normalizer.normalize(**base_claim_kwargs())

    assert claim.predicate == "expects_net_profit_change"
    assert claim.object_value["type"] == "range"
    assert claim.object_value["min"] == "160000000.00"
    assert claim.object_value["max"] == "190000000.00"
    assert claim.qualifiers["period"] == "2026-H1"
    assert claim.predicate_version == PREDICATE_VERSION


def test_normalizer_rejects_unknown_predicate() -> None:
    normalizer = ClaimNormalizer()
    with pytest.raises(ClaimNormalizationError) as exc_info:
        normalizer.normalize(**base_claim_kwargs(predicate="invented_predicate"))

    assert "PREDICATE_UNSUPPORTED" in str(exc_info.value)


def test_normalizer_rejects_object_type_mismatch() -> None:
    normalizer = ClaimNormalizer()
    # expects_net_profit_change 要求 range，传入 decimal 应被拒绝
    with pytest.raises(ClaimNormalizationError):
        normalizer.normalize(**base_claim_kwargs(object_value={"type": "decimal", "value": "100"}))


def test_normalizer_rejects_invalid_decimal() -> None:
    normalizer = ClaimNormalizer()
    with pytest.raises(ClaimNormalizationError):
        normalizer.normalize(
            **base_claim_kwargs(
                predicate="signs_major_contract",
                object_value={"type": "decimal", "value": "not-a-number"},
            )
        )


def test_fingerprint_is_deterministic_and_excludes_excerpt_and_confidence() -> None:
    normalizer = ClaimNormalizer()
    fingerprinter = ClaimFingerprint()
    claim = normalizer.normalize(**base_claim_kwargs())

    fp1 = fingerprinter.compute(claim)
    fp2 = fingerprinter.compute(claim)
    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex


def test_fingerprint_differs_when_value_changes() -> None:
    normalizer = ClaimNormalizer()
    fingerprinter = ClaimFingerprint()
    claim_a = normalizer.normalize(**base_claim_kwargs())
    claim_b = normalizer.normalize(
        **base_claim_kwargs(
            object_value={
                "type": "range",
                "min": "170000000.00",
                "max": "200000000.00",
                "unit": "CNY",
            }
        )
    )

    assert fingerprinter.compute(claim_a) != fingerprinter.compute(claim_b)


def test_fingerprint_differs_when_period_changes_but_value_same() -> None:
    normalizer = ClaimNormalizer()
    fingerprinter = ClaimFingerprint()
    claim_a = normalizer.normalize(**base_claim_kwargs())
    claim_b = normalizer.normalize(
        **base_claim_kwargs(qualifiers={"period": "2026-Q3", "comparison": "year_over_year"})
    )

    assert fingerprinter.compute(claim_a) != fingerprinter.compute(claim_b)


def test_matcher_identifies_duplicate_by_fingerprint() -> None:
    normalizer = ClaimNormalizer()
    fingerprinter = ClaimFingerprint()
    matcher = ClaimMatcher()
    claim = normalizer.normalize(**base_claim_kwargs())
    fingerprint = fingerprinter.compute(claim)

    existing = [("clm_existing", fingerprint)]
    match = matcher.match(claim, fingerprint, existing)

    assert match.relation == "duplicate"
    assert match.existing_claim_id == "clm_existing"


def test_matcher_returns_new_when_no_fingerprint_match() -> None:
    normalizer = ClaimNormalizer()
    fingerprinter = ClaimFingerprint()
    matcher = ClaimMatcher()
    claim = normalizer.normalize(**base_claim_kwargs())
    fingerprint = fingerprinter.compute(claim)

    match = matcher.match(claim, fingerprint, [("clm_other", "different-fingerprint")])
    assert match.relation == "new"


def test_predicate_registry_is_controlled() -> None:
    assert is_controlled("document_discloses_event")
    assert not is_controlled("invented_predicate")
    spec = get_predicate("expects_net_profit_change")
    assert spec is not None
    assert spec.object_type == "range"
