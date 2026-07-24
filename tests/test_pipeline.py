from datetime import datetime, timezone

from app.application.pipeline import EventResearchPipeline
from app.ingestion.blocks import PARSER_VERSION
from app.platform.repository import InMemoryRepository


def payload() -> dict:
    return {
        "source_id": "szse",
        "source_tier": "S",
        "external_id": "notice-001",
        "url": "https://example.test/notice?id=1&utm_source=test",
        "title": "示例公司（000001.SZ）2026年半年度业绩预告",
        "content": "公司预计2026年半年度净利润同比增长20%至30%。",
        "published_at": datetime(2026, 7, 12, 1, 30, tzinfo=timezone.utc),
    }


def test_vertical_slice_creates_verified_fact_card() -> None:
    repository = InMemoryRepository()
    result = EventResearchPipeline(repository).process(idempotency_key="key-1", **payload())

    assert result.status == "created"
    assert result.event.event_type == "earnings_guidance"
    assert result.event.entity_ids == ["000001.SZ"]
    assert result.claim.status == "verified"
    assert result.fact_card.status == "published"
    assert result.evidence.excerpt in result.document.content
    assert "utm_source" not in (result.document.canonical_url or "")


def test_idempotent_request_reuses_complete_result() -> None:
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository)
    first = pipeline.process(idempotency_key="same", **payload())
    second = pipeline.process(idempotency_key="same", **payload())

    assert second.status == "duplicate"
    assert second.document.id == first.document.id
    assert second.event.id == first.event.id
    assert second.fact_card.id == first.fact_card.id
    assert len(repository.documents) == 1
    assert len(repository.events) == 1


def test_lower_tier_source_requires_review() -> None:
    repository = InMemoryRepository()
    values = payload()
    values["source_tier"] = "B"
    result = EventResearchPipeline(repository).process(idempotency_key=None, **values)

    assert result.claim.status == "unverified"
    assert result.fact_card.status == "review_required"


def test_same_idempotency_key_rejects_different_request() -> None:
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository)
    pipeline.process(idempotency_key="same", **payload())
    changed = payload()
    changed["content"] = "不同公告正文"

    try:
        pipeline.process(idempotency_key="same", **changed)
    except ValueError as exc:
        assert str(exc) == "IDEMPOTENCY_CONFLICT"
    else:
        raise AssertionError("Expected idempotency conflict")


def test_same_content_from_independent_source_is_preserved() -> None:
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository)
    pipeline.process(idempotency_key=None, **payload())
    second = payload()
    second["source_id"] = "independent-media"
    second["external_id"] = "media-001"
    second["source_tier"] = "A"
    pipeline.process(idempotency_key=None, **second)

    assert len(repository.documents) == 2


def test_changed_external_document_creates_revision_and_new_fact_card() -> None:
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository)
    first = pipeline.process(idempotency_key=None, **payload())
    changed = payload()
    changed["content"] = "公司修订预计净利润同比增长30%至40%。"
    second = pipeline.process(idempotency_key=None, **changed)

    assert second.status == "revised"
    assert second.document.id == first.document.id
    assert second.event.id == first.event.id
    assert second.fact_card.version == 2
    assert second.evidence.revision_id != first.evidence.revision_id
    assert len(repository.revisions) == 2
    assert (
        repository.revisions[first.evidence.revision_id].content_hash == first.document.content_hash
    )


def test_evidence_locator_points_to_exact_original_text() -> None:
    repository = InMemoryRepository()
    result = EventResearchPipeline(repository).process(idempotency_key="loc-1", **payload())

    evidence = result.evidence
    locator = evidence.locator
    assert locator["type"] == "html"
    assert locator["block_id"] == "body-p-001"
    assert locator["char_start"] == 0
    # 引用必须逐字回溯到原文：偏移区间内的原文与 excerpt 完全一致
    assert result.document.content[locator["char_start"] : locator["char_end"]] == evidence.excerpt
    assert evidence.locator_type == "html"
    assert evidence.extraction_method == "parser"
    assert evidence.extraction_version == PARSER_VERSION


def test_evidence_uses_first_paragraph_with_correct_offset() -> None:
    repository = InMemoryRepository()
    values = payload()
    values["content"] = "首段披露：公司预计净利润同比增长20%至30%。\n后续段落不应作为首条证据。"
    result = EventResearchPipeline(repository).process(idempotency_key=None, **values)

    locator = result.evidence.locator
    assert locator["block_id"] == "body-p-001"
    assert result.document.content[locator["char_start"] : locator["char_end"]] == (
        result.evidence.excerpt
    )
    assert "首段披露" in result.evidence.excerpt
    assert "后续段落" not in result.evidence.excerpt


def test_pipeline_persists_entity_master_data_and_event_links() -> None:
    repository = InMemoryRepository()
    result = EventResearchPipeline(repository).process(idempotency_key="entity-1", **payload())

    # entity 主数据已落库
    security = repository.get_security_by_market_code("000001.SZ")
    assert security is not None
    assert repository.get_entity(security.entity_id) is not None

    # 事件-实体关联已建立，指向稳定 entity_id
    links = repository.list_event_entities(result.event.id)
    assert len(links) == 1
    assert links[0].entity_id == security.entity_id
    assert links[0].market_code == "000001.SZ"

    # Claim 的 subject_entity_id 应使用解析出的稳定 entity_id
    assert result.claim.subject_entity_id == security.entity_id


def test_pipeline_extracts_key_fields_for_earnings_guidance() -> None:
    repository = InMemoryRepository()
    values = payload()
    values["content"] = (
        "公司预计2026年半年度归属于上市公司股东的净利润16000万元至19000万元，同比增长20%至30%。"
    )
    result = EventResearchPipeline(repository).process(idempotency_key="kf-1", **values)

    assert result.event.event_type == "earnings_guidance"
    assert result.event.key_fields["period"] == "2026-半年度"
    assert result.event.key_fields["range"]["min"] == "16000"
    assert result.event.key_fields["change_rate"]["min"] == "20"
    assert result.event.missing_required == []
    assert result.event.classifier_version != ""
    # 必填齐全 -> triaged
    assert result.event.status == "triaged"


def test_pipeline_marks_event_needs_review_when_required_fields_missing() -> None:
    repository = InMemoryRepository()
    values = payload()
    # 只有同比变化，缺金额区间和利润指标
    values["content"] = "公司预计2026年半年度净利润同比增长20%至30%。"
    result = EventResearchPipeline(repository).process(idempotency_key="nr-1", **values)

    assert result.event.event_type == "earnings_guidance"
    assert result.event.status == "needs_review"
    assert "range" in result.event.missing_required
