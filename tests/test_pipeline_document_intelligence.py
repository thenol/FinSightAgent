from datetime import datetime, timezone

from app.application.pipeline import EventResearchPipeline
from app.platform.repository import InMemoryRepository


def _payload(source_id: str, external_id: str, content: str) -> dict:
    return {
        "source_id": source_id,
        "source_tier": "S",
        "external_id": external_id,
        "url": f"https://example.test/{external_id}",
        "title": "示例公司（000001.SZ）2026年半年度业绩预告",
        "content": content,
        "published_at": datetime(2026, 7, 12, 1, 30, tzinfo=timezone.utc),
    }


def test_pipeline_groups_same_content_from_different_sources() -> None:
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository)

    first = pipeline.process(
        idempotency_key=None,
        **_payload(
            "szse",
            "notice-001",
            "公司预计2026年半年度归属于上市公司股东的净利润16000万元至19000万元，同比增长20%至30%。",
        ),
    )
    second = pipeline.process(
        idempotency_key=None,
        **_payload(
            "media",
            "notice-002",
            "公司预计2026年半年度归属于上市公司股东的净利润16000万元至19000万元，同比增长20%至30%。",
        ),
    )

    assert first.event.id == second.event.id
    assert first.event.disclosure_group_id is not None
    assert second.event.disclosure_group_id == first.event.disclosure_group_id

    group = repository.get_disclosure_group(first.event.disclosure_group_id)
    assert group is not None
    members = repository.list_disclosure_group_members(group.id)
    assert {member.document_id for member in members} == {first.document.id, second.document.id}


def test_pipeline_creates_parsed_document_and_blocks() -> None:
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository)

    result = pipeline.process(
        idempotency_key=None,
        **_payload(
            "szse",
            "notice-003",
            "公司预计净利润增长。\n合同金额为1亿元。",
        ),
    )

    parsed = repository.get_parsed_document_by_document(result.document.id)
    assert parsed is not None
    assert len(parsed.block_ids) >= 1

    revision = repository.get_latest_revision(result.document.id)
    assert revision is not None
    blocks = repository.get_document_blocks_for_revision(revision.id)
    assert blocks
    for block in blocks:
        assert result.document.content[block.char_start:block.char_end] == block.text
