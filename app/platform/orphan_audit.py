"""Read-only cross-domain orphan reference audit (IMP-010)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

VERSION = "orphan-audit-v1"


@dataclass(frozen=True)
class OrphanFinding:
    check_id: str
    subject_type: str
    subject_id: str
    missing_type: str
    missing_id: str


@dataclass(frozen=True)
class OrphanSnapshot:
    document_ids: set[str]
    revision_ids: set[str]
    event_ids: set[str]
    evidence_ids: set[str]
    provider_ids: set[str]
    evidence_rows: list[tuple[str, str, str]]  # id, document_id, revision_id
    claim_rows: list[tuple[str, str, tuple[str, ...]]]  # id, event_id, evidence_ids
    workflow_rows: list[tuple[str, str]]  # id, event_id
    fact_card_rows: list[tuple[str, str]]  # id, event_id
    match_decision_rows: list[tuple[str, str]]  # id or synthetic, document_id
    binding_rows: list[tuple[str, str]]  # agent_key, provider_id
    event_document_links: list[tuple[str, str]]  # event_id, document_id


@dataclass(frozen=True)
class OrphanAuditReport:
    version: str
    generated_at: str
    finding_count: int
    findings: list[OrphanFinding]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "finding_count": self.finding_count,
            "findings": [asdict(item) for item in self.findings],
        }


def audit_snapshot(snapshot: OrphanSnapshot) -> OrphanAuditReport:
    findings: list[OrphanFinding] = []

    for evidence_id, document_id, revision_id in snapshot.evidence_rows:
        if document_id not in snapshot.document_ids:
            findings.append(
                OrphanFinding(
                    check_id="evidence.document_missing",
                    subject_type="evidence_span",
                    subject_id=evidence_id,
                    missing_type="document",
                    missing_id=document_id,
                )
            )
        if revision_id not in snapshot.revision_ids:
            findings.append(
                OrphanFinding(
                    check_id="evidence.revision_missing",
                    subject_type="evidence_span",
                    subject_id=evidence_id,
                    missing_type="document_revision",
                    missing_id=revision_id,
                )
            )

    for claim_id, event_id, evidence_ids in snapshot.claim_rows:
        if event_id not in snapshot.event_ids:
            findings.append(
                OrphanFinding(
                    check_id="claim.event_missing",
                    subject_type="claim",
                    subject_id=claim_id,
                    missing_type="event",
                    missing_id=event_id,
                )
            )
        for evidence_id in evidence_ids:
            if evidence_id not in snapshot.evidence_ids:
                findings.append(
                    OrphanFinding(
                        check_id="claim.evidence_missing",
                        subject_type="claim",
                        subject_id=claim_id,
                        missing_type="evidence_span",
                        missing_id=evidence_id,
                    )
                )

    for workflow_id, event_id in snapshot.workflow_rows:
        if event_id not in snapshot.event_ids:
            findings.append(
                OrphanFinding(
                    check_id="workflow.event_missing",
                    subject_type="workflow_run",
                    subject_id=workflow_id,
                    missing_type="event",
                    missing_id=event_id,
                )
            )

    for card_id, event_id in snapshot.fact_card_rows:
        if event_id not in snapshot.event_ids:
            findings.append(
                OrphanFinding(
                    check_id="fact_card.event_missing",
                    subject_type="fact_card",
                    subject_id=card_id,
                    missing_type="event",
                    missing_id=event_id,
                )
            )

    for decision_id, document_id in snapshot.match_decision_rows:
        if document_id not in snapshot.document_ids:
            findings.append(
                OrphanFinding(
                    check_id="match_decision.document_missing",
                    subject_type="match_decision",
                    subject_id=decision_id,
                    missing_type="document",
                    missing_id=document_id,
                )
            )

    for agent_key, provider_id in snapshot.binding_rows:
        if provider_id not in snapshot.provider_ids:
            findings.append(
                OrphanFinding(
                    check_id="llm_binding.provider_missing",
                    subject_type="llm_agent_binding",
                    subject_id=agent_key,
                    missing_type="llm_provider",
                    missing_id=provider_id,
                )
            )

    for event_id, document_id in snapshot.event_document_links:
        if document_id not in snapshot.document_ids:
            findings.append(
                OrphanFinding(
                    check_id="event.document_missing",
                    subject_type="event",
                    subject_id=event_id,
                    missing_type="document",
                    missing_id=document_id,
                )
            )

    findings.sort(key=lambda item: (item.check_id, item.subject_id, item.missing_id))
    return OrphanAuditReport(
        version=VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        finding_count=len(findings),
        findings=findings,
    )


def snapshot_from_memory(repository: Any) -> OrphanSnapshot:
    documents = getattr(repository, "documents", {})
    revisions = getattr(repository, "revisions", {})
    events = getattr(repository, "events", {})
    evidence = getattr(repository, "evidence", {})
    claims = getattr(repository, "claims", {})
    workflows = getattr(repository, "workflow_runs", {})
    fact_cards = getattr(repository, "fact_cards", {})
    match_decisions = getattr(repository, "match_decisions", [])
    providers = getattr(repository, "llm_providers", {})
    bindings = getattr(repository, "llm_agent_bindings", {})

    return OrphanSnapshot(
        document_ids=set(documents),
        revision_ids=set(revisions),
        event_ids=set(events),
        evidence_ids=set(evidence),
        provider_ids=set(providers),
        evidence_rows=[
            (item.id, item.document_id, item.revision_id) for item in evidence.values()
        ],
        claim_rows=[
            (item.id, item.event_id, tuple(item.evidence_ids or []))
            for item in claims.values()
        ],
        workflow_rows=[(item.id, item.event_id) for item in workflows.values()],
        fact_card_rows=[(item.id, item.event_id) for item in fact_cards.values()],
        match_decision_rows=[
            (item.id, item.document_id) for item in match_decisions
        ],
        binding_rows=[
            (item.agent_key, item.provider_id) for item in bindings.values()
        ],
        event_document_links=[
            (event.id, document_id)
            for event in events.values()
            for document_id in (event.document_ids or [])
        ],
    )


def snapshot_from_sqlalchemy(repository: Any) -> OrphanSnapshot:
    from sqlalchemy import select

    from app.platform.db_models import (
        ClaimModel,
        DocumentModel,
        DocumentRevisionModel,
        EventModel,
        EvidenceSpanModel,
        FactCardModel,
        LlmAgentBindingModel,
        LlmProviderModel,
        MatchDecisionModel,
        WorkflowRunModel,
    )

    session = repository.session
    document_ids = set(session.scalars(select(DocumentModel.id)))
    revision_ids = set(session.scalars(select(DocumentRevisionModel.id)))
    event_ids = set(session.scalars(select(EventModel.id)))
    evidence_models = list(session.scalars(select(EvidenceSpanModel)))
    claim_models = list(session.scalars(select(ClaimModel)))
    workflow_models = list(session.scalars(select(WorkflowRunModel)))
    fact_card_models = list(session.scalars(select(FactCardModel)))
    match_models = list(session.scalars(select(MatchDecisionModel)))
    provider_ids = set(session.scalars(select(LlmProviderModel.id)))
    binding_models = list(session.scalars(select(LlmAgentBindingModel)))
    event_models = list(session.scalars(select(EventModel)))

    return OrphanSnapshot(
        document_ids=document_ids,
        revision_ids=revision_ids,
        event_ids=event_ids,
        evidence_ids={item.id for item in evidence_models},
        provider_ids=provider_ids,
        evidence_rows=[
            (item.id, item.document_id, item.revision_id) for item in evidence_models
        ],
        claim_rows=[
            (item.id, item.event_id, tuple(item.evidence_ids or []))
            for item in claim_models
        ],
        workflow_rows=[(item.id, item.event_id) for item in workflow_models],
        fact_card_rows=[(item.id, item.event_id) for item in fact_card_models],
        match_decision_rows=[
            (item.id, item.document_id) for item in match_models
        ],
        binding_rows=[
            (item.agent_key, item.provider_id) for item in binding_models
        ],
        event_document_links=[
            (event.id, document_id)
            for event in event_models
            for document_id in (event.document_ids or [])
        ],
    )


def load_snapshot(repository: Any) -> OrphanSnapshot:
    if hasattr(repository, "session"):
        return snapshot_from_sqlalchemy(repository)
    if hasattr(repository, "documents") and hasattr(repository, "evidence"):
        return snapshot_from_memory(repository)
    raise TypeError(f"Unsupported repository for orphan audit: {type(repository)!r}")


def audit_repository(repository: Any) -> OrphanAuditReport:
    return audit_snapshot(load_snapshot(repository))


def build_repository_from_settings(settings: Any | None = None) -> Any:
    from app.platform.repository import InMemoryRepository, SqlAlchemyRepository
    from app.platform.settings import Settings

    cfg = settings or Settings.from_environment()
    if cfg.repository == "postgresql":
        return SqlAlchemyRepository(cfg.database_url)
    return InMemoryRepository()
