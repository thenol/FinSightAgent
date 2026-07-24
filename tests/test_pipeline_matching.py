from datetime import datetime, timezone

from app.application.pipeline import EventResearchPipeline
from app.platform.repository import InMemoryRepository


def payload(
    *,
    external_id: str,
    title: str,
    content: str,
    source_id: str = "official",
    source_tier: str = "S",
    published_at: datetime | None = None,
) -> dict:
    return {
        "source_id": source_id,
        "source_tier": source_tier,
        "external_id": external_id,
        "url": f"https://example.test/{external_id}",
        "title": title,
        "content": content,
        "published_at": published_at or datetime(2026, 7, 12, 9, 30, tzinfo=timezone.utc),
    }


def test_same_event_from_second_source_merges_into_existing_event() -> None:
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository)
    first = pipeline.process(
        idempotency_key=None,
        **payload(
            external_id="official-1",
            title="示例公司（000001.SZ）重大合同公告",
            content="公司与甲方签订重大合同，合同金额为15000万元。",
        ),
    )
    second = pipeline.process(
        idempotency_key=None,
        **payload(
            external_id="official-2",
            title="示例公司（000001.SZ）重大合同公告补充说明",
            content="公司与甲方签订重大合同，合同金额为15000万元。",
            source_id="independent-media",
            source_tier="A",
        ),
    )

    assert second.event.id == first.event.id
    assert len(repository.events) == 1
    assert len(second.event.document_ids) == 2
    assert repository.list_match_decisions(second.document.id)[0].decision == "merged"


def test_period_conflict_creates_review_task_instead_of_merging() -> None:
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository)
    pipeline.process(
        idempotency_key=None,
        **payload(
            external_id="period-1",
            title="示例公司（000001.SZ）2026年半年度业绩预告",
            content="公司预计2026年半年度净利润16000万元至19000万元，同比增长20%至30%。",
        ),
    )
    second = pipeline.process(
        idempotency_key=None,
        **payload(
            external_id="period-2",
            title="示例公司（000001.SZ）2026年年度业绩预告",
            content="公司预计2026年年度净利润16000万元至19000万元，同比增长20%至30%。",
        ),
    )

    assert len(repository.events) == 2
    assert repository.list_match_decisions(second.document.id)[0].decision == "review"
    assert any(task.document_id == second.document.id for task in repository.merge_review_tasks)


def test_importance_uses_source_and_event_features() -> None:
    repository = InMemoryRepository()
    result = EventResearchPipeline(repository).process(
        idempotency_key=None,
        **payload(
            external_id="penalty-1",
            title="示例公司（000001.SZ）收到监管处罚",
            content="公司收到证监会行政处罚决定书。",
            # 使用近实时发布时间，避免 recency 衰减导致断言随日历漂移
            published_at=datetime.now(timezone.utc),
        ),
    )

    assert result.event.importance >= 0.85
    assert result.event.urgency in {"high", "critical"}

