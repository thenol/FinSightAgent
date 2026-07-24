from app.events.entities import EntityResolver
from app.events.service import EventService
from app.platform.repository import InMemoryRepository


def document_text(market_code: str = "000001.SZ", name: str = "示例公司") -> str:
    return f"{name}（{market_code}）2026年半年度业绩预告\n公司预计净利润同比增长20%至30%。"


def test_code_exact_match_creates_master_data_and_resolves() -> None:
    repository = InMemoryRepository()
    resolver = EntityResolver(repository)

    resolutions = resolver.resolve(document_text(), "doc_1")

    assert len(resolutions) == 1
    resolution = resolutions[0]
    assert resolution.market_code == "000001.SZ"
    assert resolution.entity_id is not None
    assert resolution.confidence == 1.0
    assert resolution.resolution_method == "code_exact_auto_created"
    assert not resolution.ambiguous
    # 主数据已建立
    assert repository.get_security_by_market_code("000001.SZ") is not None
    assert repository.get_entity(resolution.entity_id) is not None


def test_repeated_resolution_reuses_existing_master_data() -> None:
    repository = InMemoryRepository()
    resolver = EntityResolver(repository)

    first = resolver.resolve(document_text(), "doc_1")
    second = resolver.resolve(document_text(), "doc_2")

    assert first[0].entity_id == second[0].entity_id
    assert second[0].resolution_method == "code_exact"
    # 只创建一份主数据
    assert len(repository.securities) == 1
    assert len(repository.entities) == 1


def test_exchange_suffix_inference_for_sh_codes() -> None:
    repository = InMemoryRepository()
    resolver = EntityResolver(repository)

    resolutions = resolver.resolve(document_text("600000.SH", "浦发银行"), "doc_1")

    assert resolutions[0].market_code == "600000.SH"
    security = repository.get_security_by_market_code("600000.SH")
    assert security is not None
    assert security.exchange == "SH"


def test_exchange_suffix_inferred_when_missing() -> None:
    repository = InMemoryRepository()
    resolver = EntityResolver(repository)

    # 不带后缀的 6 开头代码应推断为 SH
    resolutions = resolver.resolve("浦发银行（600000）公告", "doc_1")

    assert resolutions[0].market_code == "600000.SH"


def test_multiple_codes_resolve_independently() -> None:
    repository = InMemoryRepository()
    resolver = EntityResolver(repository)

    text = "甲方 000001.SZ 与乙方 600000.SH 签订合同"
    resolutions = resolver.resolve(text, "doc_1")

    codes = {resolution.market_code for resolution in resolutions}
    assert codes == {"000001.SZ", "600000.SH"}


def test_event_service_writes_entity_links_and_keeps_market_codes() -> None:
    repository = InMemoryRepository()
    service = EventService(repository)

    from datetime import datetime, timezone

    from app.domain import Document

    document = Document(
        id="doc_1",
        source_id="szse",
        source_tier="S",
        external_id="notice-1",
        canonical_url="https://example.test/1",
        title="示例公司（000001.SZ）2026年半年度业绩预告",
        content="公司预计净利润同比增长20%至30%。",
        content_hash="hash-1",
        published_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )
    event = service.create_event(document)

    # entity_ids 保留 market_code 以兼容前端
    assert event.entity_ids == ["000001.SZ"]
    # event_entities 关联已建立，指向稳定 entity_id
    links = repository.list_event_entities(event.id)
    assert len(links) == 1
    assert links[0].market_code == "000001.SZ"
    assert links[0].confidence == 1.0
    assert links[0].resolution_method == "code_exact_auto_created"


def test_no_securities_when_document_has_no_codes() -> None:
    repository = InMemoryRepository()
    resolver = EntityResolver(repository)

    resolutions = resolver.resolve("某公司发布公告，不含代码", "doc_1")

    assert resolutions == []
    assert repository.securities == {}
