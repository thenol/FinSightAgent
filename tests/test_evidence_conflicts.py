from datetime import datetime, timezone

from app.application.pipeline import EventResearchPipeline
from app.events.service import EventService
from app.evidence.service import EvidenceService
from app.ingestion.artifacts import InMemoryArtifactStore
from app.ingestion.service import IngestionService
from app.platform.repository import InMemoryRepository
from app.publishing.service import FactCardService


def _first_document() -> dict:
    return {
        "source_id": "szse",
        "source_tier": "S",
        "external_id": "notice-conflict-001",
        "url": "https://example.test/notice-conflict?id=1",
        "title": "示例公司（000001.SZ）2026年半年度业绩预告",
        "content": (
            "公司预计2026年半年度归属于上市公司股东的净利润"
            "16000万元至19000万元，同比增长20%至30%。"
        ),
        "published_at": datetime(2026, 7, 12, 1, 30, tzinfo=timezone.utc),
    }


def _conflicting_document() -> dict:
    return {
        "source_id": "szse",
        "source_tier": "S",
        "external_id": "notice-conflict-002",
        "url": "https://example.test/notice-conflict?id=2",
        "title": "示例公司（000001.SZ）2026年半年度业绩预告",
        "content": (
            "公司预计2026年半年度归属于上市公司股东的净利润"
            "25000万元至28000万元，同比增长40%至50%。"
        ),
        "published_at": datetime(2026, 7, 13, 1, 30, tzinfo=timezone.utc),
    }


def test_conflicting_claims_are_marked_and_block_fact_card() -> None:
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository)
    first = pipeline.process(idempotency_key=None, **_first_document())

    assert first.event.event_type == "earnings_guidance"
    assert first.claim.status == "verified"
    assert first.fact_card.status == "published"

    with repository.transaction() as repo:
        ingestion = IngestionService(repo, InMemoryArtifactStore())
        events = EventService(repo)
        evidence_service = EvidenceService(repo)
        fact_cards = FactCardService(repo)

        document, _ = ingestion.ingest(**_conflicting_document())
        event = events.attach_document_to_event(first.event, document)
        _, new_claims = evidence_service.register_event_claims(document, event)

    conflicts = repository.list_conflicts_for_event(event.id)
    assert conflicts
    assert any(conflict.severity == "critical" for conflict in conflicts)

    all_claims = repository.get_claims_for_event(event.id)
    assert all(claim.status == "conflicted" for claim in all_claims)

    profit_claim_id = next(
        claim.id for claim in new_claims if claim.predicate == "expects_net_profit"
    )
    profit_claim = repository.get_claim(profit_claim_id)
    assert profit_claim is not None
    assert profit_claim.status == "conflicted"

    card = fact_cards.create(event, profit_claim)
    assert card.status == "needs_review"

    review_tasks = [
        task
        for task in repository.list_review_tasks()
        if task.object_type == "report" and task.object_id == card.id
    ]
    assert len(review_tasks) == 1
    assert review_tasks[0].reason_code == "CLAIM_CONFLICT"
