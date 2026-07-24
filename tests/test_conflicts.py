from app.evidence.claims import ClaimNormalizer
from app.evidence.conflicts import ConflictDetector


def normalize(**overrides):
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
    return ClaimNormalizer().normalize(**kwargs)


def test_value_conflict_is_critical_for_range() -> None:
    detector = ConflictDetector()
    left = normalize()
    right = normalize(
        object_value={"type": "range", "min": "200000000.00", "max": "230000000.00", "unit": "CNY"}
    )

    conflict = detector.detect(left, right, "clm_a", "clm_b")

    assert conflict is not None
    assert conflict.conflict_type == "value"
    assert conflict.severity == "critical"
    assert conflict.claim_ids == ["clm_a", "clm_b"]


def test_unit_conflict_is_major() -> None:
    detector = ConflictDetector()
    left = normalize()
    right = normalize(
        object_value={"type": "range", "min": "160000000.00", "max": "190000000.00", "unit": "USD"}
    )

    conflict = detector.detect(left, right)

    assert conflict.conflict_type == "unit"
    assert conflict.severity == "major"


def test_period_conflict_is_critical() -> None:
    detector = ConflictDetector()
    left = normalize()
    right = normalize(qualifiers={"period": "2026-Q3", "comparison": "year_over_year"})

    conflict = detector.detect(left, right)

    assert conflict.conflict_type == "period"
    assert conflict.severity == "critical"


def test_subject_conflict_is_critical() -> None:
    detector = ConflictDetector()
    left = normalize()
    right = normalize(subject_entity_id="ent_002", subject_text="另一公司")

    conflict = detector.detect(left, right)

    assert conflict.conflict_type == "subject"
    assert conflict.severity == "critical"


def test_scope_conflict_is_major() -> None:
    detector = ConflictDetector()
    left = normalize(
        qualifiers={
            "period": "2026-H1",
            "comparison": "year_over_year",
            "accounting_scope": "attributable_to_parent",
        }
    )
    right = normalize(
        qualifiers={
            "period": "2026-H1",
            "comparison": "year_over_year",
            "accounting_scope": "consolidated",
        }
    )

    conflict = detector.detect(left, right)

    assert conflict.conflict_type == "scope"
    assert conflict.severity == "major"


def test_no_conflict_when_claims_agree() -> None:
    detector = ConflictDetector()
    left = normalize()
    right = normalize()

    assert detector.detect(left, right) is None
