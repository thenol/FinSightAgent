from datetime import datetime, timezone

from app.domain import (
    Claim,
    Document,
    DocumentRevision,
    Event,
    EvidenceSpan,
    LlmAgentBinding,
    LlmProviderConfig,
    MatchDecision,
    WorkflowRun,
)
from app.platform.orphan_audit import audit_repository, audit_snapshot, snapshot_from_memory
from app.platform.repository import InMemoryRepository

NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _document(document_id: str = "doc-1") -> Document:
    return Document(
        id=document_id,
        source_id="src-1",
        source_tier="A",
        external_id=f"ext-{document_id}",
        canonical_url=None,
        title="t",
        content="c",
        content_hash=f"h-{document_id}",
        published_at=NOW,
        ingested_at=NOW,
    )


def _revision(document_id: str = "doc-1", revision_id: str = "rev-1") -> DocumentRevision:
    return DocumentRevision(
        id=revision_id,
        document_id=document_id,
        revision_no=1,
        artifact_id="art-1",
        content_hash=f"h-{document_id}",
        normalized_content_uri=f"memory://{document_id}",
        parser_version="v1",
        created_at=NOW,
    )


def test_orphan_audit_reports_missing_document_and_event() -> None:
    repo = InMemoryRepository()
    repo.save_document(_document())
    repo.save_document_revision(_revision())
    repo.save_evidence(
        EvidenceSpan(
            id="evd-orphan-doc",
            document_id="doc-missing",
            revision_id="rev-1",
            locator={},
            excerpt="x",
            excerpt_hash="eh",
            locator_type="html",
            extraction_method="rule",
            extraction_version="v1",
            created_at=NOW,
        )
    )
    repo.save_claim(
        Claim(
            id="clm-1",
            event_id="evt-missing",
            subject_text="s",
            predicate="p",
            object_value={},
            status="unverified",
            confidence=0.5,
            evidence_ids=["evd-orphan-doc"],
            as_of=NOW,
        )
    )
    repo.save_workflow_run(
        WorkflowRun(
            id="wf-1",
            event_id="evt-missing",
            trigger_id="manual",
            status="pending",
            as_of=NOW,
        )
    )

    report = audit_repository(repo)
    check_ids = {item.check_id for item in report.findings}
    assert "evidence.document_missing" in check_ids
    assert "claim.event_missing" in check_ids
    assert "workflow.event_missing" in check_ids
    assert report.finding_count >= 3


def test_orphan_audit_clean_graph_has_no_findings() -> None:
    repo = InMemoryRepository()
    repo.save_document(_document())
    repo.save_document_revision(_revision())
    repo.save_event(
        Event(
            id="evt-1",
            event_type="earnings_guidance",
            status="active",
            title="e",
            entity_ids=[],
            document_ids=["doc-1"],
            importance=0.5,
            urgency="normal",
            occurred_at=NOW,
        )
    )
    repo.save_evidence(
        EvidenceSpan(
            id="evd-1",
            document_id="doc-1",
            revision_id="rev-1",
            locator={},
            excerpt="x",
            excerpt_hash="eh",
            locator_type="html",
            extraction_method="rule",
            extraction_version="v1",
            created_at=NOW,
        )
    )
    repo.save_claim(
        Claim(
            id="clm-1",
            event_id="evt-1",
            subject_text="s",
            predicate="p",
            object_value={},
            status="unverified",
            confidence=0.5,
            evidence_ids=["evd-1"],
            as_of=NOW,
        )
    )
    repo.save_match_decision(
        MatchDecision(
            id="md-1",
            document_id="doc-1",
            candidate_event_id="evt-1",
            features={},
            score=0.9,
            rule_version="v1",
            decision="merged",
            created_at=NOW,
        )
    )
    repo.save_llm_provider(
        LlmProviderConfig(
            id="prov-1",
            code="det",
            display_name="Deterministic",
            protocol="deterministic",
            base_url="",
            api_key_encrypted="",
            model="stub",
            status="active",
            is_default=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    repo.llm_agent_bindings["classifier"] = LlmAgentBinding(
        agent_key="classifier",
        provider_id="prov-1",
        model_override=None,
        updated_at=NOW,
    )

    report = audit_snapshot(snapshot_from_memory(repo))
    assert report.finding_count == 0
