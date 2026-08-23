import hashlib
from datetime import datetime, timezone

from app.domain import (
    Claim,
    ClaimEvidenceRelation,
    ConflictRecord,
    Document,
    Event,
    EvidenceSpan,
    FactCard,
    MatchDecision,
    MergeReviewTask,
    ReviewTask,
)
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository
from app.platform.settings import Settings
from app.review.schemas import AutoReviewDecision
from app.review.service import AutoReviewService


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_repo() -> InMemoryRepository:
    return InMemoryRepository()


def _save_document(repo: InMemoryRepository, source_tier: str) -> Document:
    doc = Document(
        id=new_id("doc"),
        source_id="src_test",
        source_tier=source_tier,
        external_id="ext-001",
        canonical_url="https://example.test/001",
        title="Test announcement",
        content="The company signed a major contract.",
        content_hash=_content_hash("content"),
        published_at=datetime.now(timezone.utc),
        ingested_at=datetime.now(timezone.utc),
    )
    repo.save_document(doc)
    return doc


def _save_event(repo: InMemoryRepository, **overrides) -> Event:
    event = Event(
        id=new_id("evt"),
        event_type="major_contract",
        status="triaged",
        title="Test event",
        entity_ids=["600000.SH"],
        document_ids=[],
        importance=0.8,
        urgency="normal",
        occurred_at=datetime.now(timezone.utc),
        missing_required=[],
        **overrides,
    )
    repo.save_event(event)
    return event


def _save_evidence(repo: InMemoryRepository, document: Document) -> EvidenceSpan:
    evidence = EvidenceSpan(
        id=new_id("evd"),
        document_id=document.id,
        revision_id=new_id("rev"),
        locator={"type": "title"},
        excerpt=document.title,
        excerpt_hash=_content_hash(document.title),
        locator_type="title",
        extraction_method="fallback",
        extraction_version="v1",
        created_at=datetime.now(timezone.utc),
    )
    repo.save_evidence(evidence)
    return evidence


def _save_claim(
    repo: InMemoryRepository,
    event: Event,
    document: Document,
    evidence: EvidenceSpan,
    **overrides,
) -> Claim:
    object_value = overrides.pop("object_value", {"type": "string", "value": "contract"})
    claim = Claim(
        id=new_id("clm"),
        event_id=event.id,
        subject_text="600000.SH",
        predicate="signed_major_contract",
        object_value=object_value,
        status="unverified",
        confidence=0.7,
        evidence_ids=[evidence.id],
        as_of=datetime.now(timezone.utc),
        **overrides,
    )
    repo.save_claim(claim)
    repo.save_claim_evidence(
        ClaimEvidenceRelation(
            claim_id=claim.id,
            evidence_id=evidence.id,
            stance="support",
            source_independence_key=document.id,
        )
    )
    return claim


def _report_task(repo: InMemoryRepository, card: FactCard) -> ReviewTask:
    task = ReviewTask(
        id=new_id("rvt"),
        object_type="report",
        object_id=card.id,
        reason_code="REPORT_REVIEW_REQUIRED",
        allowed_decisions=["approve", "return", "reject"],
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    repo.save_review_task(task)
    return task


def _conflict_task(repo: InMemoryRepository, conflict: ConflictRecord) -> ReviewTask:
    task = ReviewTask(
        id=new_id("rvt"),
        object_type="claim_conflict",
        object_id=conflict.id,
        reason_code="CONFLICT_AMOUNT",
        allowed_decisions=["approve", "reject"],
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    repo.save_review_task(task)
    return task


def test_report_auto_approved_for_s_tier() -> None:
    repo = _make_repo()
    document = _save_document(repo, "S")
    event = _save_event(repo)
    evidence = _save_evidence(repo, document)
    claim = _save_claim(repo, event, document, evidence)
    card = FactCard(
        id=new_id("rpt"),
        event_id=event.id,
        version=1,
        status="review_required",
        title=event.title,
        summary="summary",
        claim_ids=[claim.id],
        as_of=datetime.now(timezone.utc),
    )
    repo.save_fact_card(card)
    task = _report_task(repo, card)

    decision = AutoReviewService(repo).attempt_report_review(task, card)

    assert decision is not None
    assert decision.decision == "approve"
    updated_task = repo.get_review_task(task.id)
    assert updated_task.status == "decided"
    assert updated_task.decision == "approve"


def test_report_escalates_for_b_tier() -> None:
    repo = _make_repo()
    document = _save_document(repo, "B")
    event = _save_event(repo)
    evidence = _save_evidence(repo, document)
    claim = _save_claim(repo, event, document, evidence)
    card = FactCard(
        id=new_id("rpt"),
        event_id=event.id,
        version=1,
        status="review_required",
        title=event.title,
        summary="summary",
        claim_ids=[claim.id],
        as_of=datetime.now(timezone.utc),
    )
    repo.save_fact_card(card)
    task = _report_task(repo, card)

    decision = AutoReviewService(repo).attempt_report_review(task, card)

    assert decision is None
    assert repo.get_review_task(task.id).status == "pending"


def test_conflict_auto_resolves_when_source_tiers_differ() -> None:
    repo = _make_repo()
    event = _save_event(repo)

    doc_s = _save_document(repo, "S")
    ev_s = _save_evidence(repo, doc_s)
    claim_s = _save_claim(repo, event, doc_s, ev_s, object_value={"type": "decimal", "value": 5})

    doc_b = _save_document(repo, "B")
    ev_b = _save_evidence(repo, doc_b)
    claim_b = _save_claim(repo, event, doc_b, ev_b, object_value={"type": "decimal", "value": 4.8})

    conflict = ConflictRecord(
        id=new_id("cfl"),
        event_id=event.id,
        conflict_type="amount",
        severity="major",
        status="open",
        summary="amount mismatch",
        claim_ids=[claim_s.id, claim_b.id],
    )
    repo.save_conflict(conflict)
    task = _conflict_task(repo, conflict)

    decision = AutoReviewService(repo).attempt_conflict_review(task, conflict)

    assert decision is not None
    assert decision.decision == "approve"
    assert repo.get_review_task(task.id).status == "decided"
    assert repo.get_claim(claim_s.id).status == "verified"
    assert repo.get_claim(claim_b.id).status == "rejected"
    resolved = repo.get_conflict(conflict.id)
    assert resolved.status == "resolved"


def test_conflict_escalates_when_source_tiers_same() -> None:
    repo = _make_repo()
    event = _save_event(repo)

    doc_a = _save_document(repo, "A")
    ev_a = _save_evidence(repo, doc_a)
    claim_a = _save_claim(repo, event, doc_a, ev_a, object_value={"type": "decimal", "value": 5})

    doc_a2 = _save_document(repo, "A")
    ev_a2 = _save_evidence(repo, doc_a2)
    claim_a2 = _save_claim(
        repo, event, doc_a2, ev_a2, object_value={"type": "decimal", "value": 4.8}
    )

    conflict = ConflictRecord(
        id=new_id("cfl"),
        event_id=event.id,
        conflict_type="amount",
        severity="major",
        status="open",
        summary="amount mismatch",
        claim_ids=[claim_a.id, claim_a2.id],
    )
    repo.save_conflict(conflict)
    task = _conflict_task(repo, conflict)

    decision = AutoReviewService(repo).attempt_conflict_review(task, conflict)

    assert decision is None
    assert repo.get_review_task(task.id).status == "pending"


def test_merge_review_auto_new_event_for_low_score() -> None:
    repo = _make_repo()
    document = _save_document(repo, "A")
    decision = MatchDecision(
        id=new_id("mcd"),
        document_id=document.id,
        candidate_event_id=new_id("evt"),
        features={},
        score=0.1,
        rule_version="v1",
        decision="review",
        created_at=datetime.now(timezone.utc),
    )
    repo.save_match_decision(decision)
    task = MergeReviewTask(
        id=new_id("mrt"),
        document_id=document.id,
        candidates=[decision.candidate_event_id],
        status="open",
        created_at=datetime.now(timezone.utc),
    )
    repo.save_merge_review_task(task)

    settings = Settings(
        environment="test",
        repository="memory",
        database_url="",
        redis_url="",
        artifact_root=".data/artifacts",
        jwt_secret="test-secret-32-bytes-long!!",
        bootstrap_admin_username="",
        bootstrap_admin_password="",
        auto_review_enabled_types={"merge_review"},
    )
    result = AutoReviewService(repo, settings=settings).attempt_merge_review(task)

    assert result is not None
    assert result.decision == "new_event"
    updated = repo.get_merge_review_task(task.id)
    assert updated.status == "decided"
    assert updated.decision == "new_event"


def test_min_confidence_threshold_blocks_weak_llm_decision() -> None:
    decision = AutoReviewDecision(decision="approve", confidence=0.5, reason="weak", escalate=False)
    assert decision.confidence < 0.85
