from datetime import datetime, timezone

from app.domain import Claim
from app.publishing.assembler import DEFAULT_DISCLAIMER
from app.publishing.guardrail import (
    GUARDRAIL_VERSION,
    GuardrailEngine,
)


def verified_claim(claim_id: str = "clm_1", evidence_ids: list[str] | None = None) -> Claim:
    return Claim(
        id=claim_id,
        event_id="evt_g",
        subject_text="000001.SZ",
        predicate="document_discloses_event",
        object_value={},
        status="verified",
        confidence=0.9,
        evidence_ids=evidence_ids if evidence_ids is not None else ["evd_1"],
        as_of=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )


def base_draft(**overrides) -> dict:
    draft = {
        "schema_version": "1.0.0",
        "report_type": "research_card",
        "event_id": "evt_g",
        "as_of": "2026-07-12T12:00:00+00:00",
        "title": "测试报告",
        "summary": "基于公告分析",
        "signal": "moderately_positive",
        "confidence": 0.65,
        "claim_ids": ["clm_1"],
        "analysis_ids": ["company_analysis"],
        "sections": [
            {"kind": "verified_facts", "title": "已验证事实", "items": [{"claim_id": "clm_1"}]},
        ],
        "disclaimer": DEFAULT_DISCLAIMER,
    }
    draft.update(overrides)
    return draft


def test_guardrail_passes_valid_draft() -> None:
    result = GuardrailEngine().evaluate(
        base_draft(), [verified_claim()], datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )
    assert result.passed is True
    assert result.version == GUARDRAIL_VERSION
    assert all(r.status == "pass" or r.status == "warn" for r in result.rules)


def test_guardrail_blocks_when_no_citations() -> None:
    result = GuardrailEngine().evaluate(
        base_draft(claim_ids=[]),
        [verified_claim()],
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
    )
    assert result.passed is False
    assert any(r.rule == "citation_integrity" and r.status == "fail" for r in result.rules)


def test_guardrail_blocks_when_claim_lacks_evidence() -> None:
    result = GuardrailEngine().evaluate(
        base_draft(claim_ids=["clm_1"]),
        [verified_claim(evidence_ids=[])],
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
    )
    assert result.passed is False
    assert any(r.rule == "citation_integrity" and r.status == "fail" for r in result.rules)


def test_guardrail_blocks_forbidden_phrases() -> None:
    result = GuardrailEngine().evaluate(
        base_draft(summary="建议买入，保证收益"),
        [verified_claim()],
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
    )
    assert result.passed is False
    assert any(r.rule == "forbidden_phrases" and r.status == "fail" for r in result.rules)


def test_guardrail_warns_low_confidence_strong_signal() -> None:
    result = GuardrailEngine().evaluate(
        base_draft(confidence=0.30, signal="strongly_positive"),
        [verified_claim()],
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
    )
    low_conf = next(r for r in result.rules if r.rule == "low_confidence_signal")
    assert low_conf.status == "warn"
    assert "降级标签" in (low_conf.fix_suggestion or "")


def test_guardrail_blocks_missing_required_fields() -> None:
    result = GuardrailEngine().evaluate(
        base_draft(disclaimer=""),
        [verified_claim()],
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
    )
    assert result.passed is False
    assert any(r.rule == "required_fields" and r.status == "fail" for r in result.rules)


def test_guardrail_blocks_empty_verified_facts_section() -> None:
    result = GuardrailEngine().evaluate(
        base_draft(sections=[{"kind": "verified_facts", "title": "已验证事实", "items": []}]),
        [verified_claim()],
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
    )
    assert result.passed is False
    assert any(r.rule == "section_partition" and r.status == "fail" for r in result.rules)
