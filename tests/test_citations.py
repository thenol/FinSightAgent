from datetime import datetime, timezone

from app.domain import Claim, EvidenceSpan, FactCard
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository
from app.publishing.citations import CitationResolver


def _seed(repository: InMemoryRepository, source_tier: str = "S") -> tuple[Claim, str]:
    evidence_id = new_id("evd")
    repository.save_evidence(
        EvidenceSpan(
            id=evidence_id,
            document_id="doc_1",
            revision_id="rev_1",
            locator={"type": "html", "block_id": "body-p-001", "char_start": 0, "char_end": 10},
            excerpt="公告原文片段" * 30,
            excerpt_hash="h",
            locator_type="html",
            extraction_method="parser",
            extraction_version="html-blocks-v1",
            created_at=datetime.now(timezone.utc),
        )
    )
    claim = Claim(
        id=new_id("clm"),
        event_id="evt_c",
        subject_text="000001.SZ",
        predicate="document_discloses_event",
        object_value={},
        status="verified",
        confidence=0.9,
        evidence_ids=[evidence_id],
        as_of=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )
    repository.save_claim(claim)
    return claim, source_tier


def test_resolver_returns_full_excerpt_for_s_tier_researcher() -> None:
    repository = InMemoryRepository()
    claim, tier = _seed(repository, source_tier="S")
    resolver = CitationResolver(repository)

    result = resolver.resolve(claim, role="researcher", document_source_tier=tier)
    assert result is not None
    assert result["display_scope"] == "full"
    assert result["excerpt"] is not None
    assert result["locator"]["block_id"] == "body-p-001"


def test_resolver_returns_excerpt_for_a_tier() -> None:
    repository = InMemoryRepository()
    claim, tier = _seed(repository, source_tier="A")
    resolver = CitationResolver(repository)

    result = resolver.resolve(claim, role="researcher", document_source_tier=tier)
    assert result["display_scope"] == "excerpt"
    assert len(result["excerpt"]) <= 200


def test_resolver_returns_entry_for_c_tier_researcher() -> None:
    repository = InMemoryRepository()
    claim, tier = _seed(repository, source_tier="C")
    resolver = CitationResolver(repository)

    result = resolver.resolve(claim, role="researcher", document_source_tier=tier)
    assert result["display_scope"] == "entry"
    assert result["excerpt"] is None


def test_resolver_returns_entry_only_for_external_role() -> None:
    repository = InMemoryRepository()
    claim, tier = _seed(repository, source_tier="S")
    resolver = CitationResolver(repository)

    result = resolver.resolve(claim, role="external", document_source_tier=tier)
    assert result["display_scope"] == "entry"
    assert result["excerpt"] is None


def test_resolve_report_masks_external_but_keeps_researcher_excerpt() -> None:
    repository = InMemoryRepository()
    claim, _ = _seed(repository, source_tier="A")
    report = FactCard(
        id=new_id("rpt"),
        event_id=claim.event_id,
        version=1,
        status="draft",
        title="t",
        summary="s",
        claim_ids=[claim.id],
        as_of=claim.as_of,
    )
    resolver = CitationResolver(repository)

    external = resolver.resolve_report(report, role="external")
    researcher = resolver.resolve(claim, role="researcher", document_source_tier="A")

    assert len(external) == 1
    assert external[0]["display_scope"] == "entry"
    assert external[0]["excerpt"] is None
    assert researcher is not None
    assert researcher["display_scope"] == "excerpt"
    assert researcher["excerpt"]


def test_authorized_document_content_hides_body_for_publisher_channel() -> None:
    resolver = CitationResolver(InMemoryRepository())
    role = CitationResolver.citation_role_for_api("publisher")
    scope, content = resolver.authorized_document_content(
        "机密正文",
        role=role,
        source_tier="S",
    )
    assert role == "external"
    assert scope == "entry"
    assert content is None

    scope_s, content_s = resolver.authorized_document_content(
        "机密正文",
        role="researcher",
        source_tier="S",
    )
    assert scope_s == "full"
    assert content_s == "机密正文"


def test_license_entry_only_overrides_s_tier_for_researcher() -> None:
    repository = InMemoryRepository()
    claim, _ = _seed(repository, source_tier="S")
    resolver = CitationResolver(repository)

    result = resolver.resolve(
        claim,
        role="researcher",
        document_source_tier="S",
        license="entry_only",
    )
    assert result is not None
    assert result["display_scope"] == "entry"
    assert result["excerpt"] is None

    scope, content = resolver.authorized_document_content(
        "机密正文",
        role="researcher",
        source_tier="S",
        license="entry_only",
    )
    assert scope == "entry"
    assert content is None


def test_resolve_loads_source_license_from_repository() -> None:
    from app.domain import Document, Source

    repository = InMemoryRepository()
    claim, _ = _seed(repository, source_tier="S")
    repository.save_source(
        Source(
            id="src-license",
            code="license-source",
            name="License source",
            trust_tier="S",
            feed_url="https://example.test/feed.xml",
            allowed_domains=["example.test"],
            license="entry_only",
        )
    )
    repository.save_document(
        Document(
            id="doc_1",
            source_id="src-license",
            source_tier="S",
            external_id="ext-1",
            canonical_url="https://example.test/1",
            title="t",
            content="全文",
            content_hash="h",
            published_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
            ingested_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        )
    )
    resolver = CitationResolver(repository)
    result = resolver.resolve(claim, role="researcher")
    assert result is not None
    assert result["display_scope"] == "entry"
    assert result["excerpt"] is None


def test_resolver_returns_none_when_no_evidence() -> None:
    repository = InMemoryRepository()
    claim = Claim(
        id=new_id("clm"),
        event_id="evt_c",
        subject_text="x",
        predicate="p",
        object_value={},
        status="verified",
        confidence=0.9,
        evidence_ids=[],
        as_of=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )
    repository.save_claim(claim)
    resolver = CitationResolver(repository)

    assert resolver.resolve(claim, role="researcher", document_source_tier="S") is None
