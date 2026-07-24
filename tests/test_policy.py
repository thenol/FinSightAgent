from app.evidence.claims import ClaimNormalizer
from app.evidence.policy import (
    POLICY_VERSION,
    EvidencePolicyService,
    EvidenceRecord,
    source_independence_key,
)


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


def record(tier="S", stance="support", key="filing-001", evidence_id="evd_1"):
    return EvidenceRecord(
        evidence_id=evidence_id, source_tier=tier, stance=stance, source_independence_key=key
    )


def test_s_tier_direct_evidence_verifies() -> None:
    policy = EvidencePolicyService()
    decision = policy.decide(normalize(), [record(tier="S")])

    assert decision.status == "verified"
    assert decision.reason_code == "S_TIER_DIRECT_EVIDENCE"
    assert decision.confidence >= 0.80
    assert decision.policy_version == POLICY_VERSION


def test_a_tier_with_independent_support_verifies() -> None:
    policy = EvidencePolicyService()
    decision = policy.decide(
        normalize(),
        [record(tier="A", key="media-001"), record(tier="B", key="media-002")],
    )

    assert decision.status == "verified"
    assert decision.reason_code == "A_TIER_WITH_INDEPENDENT_SUPPORT"


def test_single_a_tier_without_independent_support_is_unverified() -> None:
    policy = EvidencePolicyService()
    decision = policy.decide(normalize(), [record(tier="A", key="media-001")])

    assert decision.status == "unverified"


def test_low_tier_only_is_unverified() -> None:
    policy = EvidencePolicyService()
    decision = policy.decide(normalize(), [record(tier="C", key="social-001")])

    assert decision.status == "unverified"
    assert decision.reason_code == "LOW_TIER_ONLY"


def test_syndicated_reposts_count_as_one_independent_source() -> None:
    policy = EvidencePolicyService()
    # 十篇转载共享同一 source_independence_key，只计一个独立来源
    reposts = [record(tier="A", key="wire-root-001", evidence_id=f"evd_{i}") for i in range(10)]
    decision = policy.decide(normalize(), reposts)

    assert decision.independent_source_count == 1
    # 单一独立来源的 A 级不足以 verified
    assert decision.status == "unverified"


def test_critical_conflict_forces_conflicted_status() -> None:
    policy = EvidencePolicyService()
    decision = policy.decide(normalize(), [record(tier="S")], has_critical_conflict=True)

    assert decision.status == "conflicted"
    assert decision.reason_code == "CRITICAL_VALUE_CONFLICT"


def test_refuting_evidence_forces_conflicted_status() -> None:
    policy = EvidencePolicyService()
    decision = policy.decide(
        normalize(),
        [
            record(tier="S", stance="support", key="filing-001"),
            record(tier="A", stance="refute", key="media-002"),
        ],
    )

    assert decision.status == "conflicted"
    assert decision.reason_code == "COUNTER_EVIDENCE_PRESENT"


def test_no_supporting_evidence_is_unverified() -> None:
    policy = EvidencePolicyService()
    decision = policy.decide(normalize(), [])

    assert decision.status == "unverified"
    assert decision.reason_code == "NO_DIRECT_EVIDENCE"


def test_source_independence_key_priority() -> None:
    # 监管文件 ID 优先于公司披露 ID
    assert source_independence_key(filing_id="F-1", disclosure_id="D-1") == "F-1"
    # 公司披露 ID 优先于媒体
    assert source_independence_key(disclosure_id="D-1", media_org_id="M-1") == "D-1"
    # 转载共享传播链根文档
    assert source_independence_key(propagation_root="wire-root-001") == "wire-root-001"
