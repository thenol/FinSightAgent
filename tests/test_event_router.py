"""Event Router：规则提名 + 模型确认。"""

from datetime import datetime, timezone

from app.application.pipeline import EventResearchPipeline
from app.domain import Document
from app.events.router import EventRouter, deterministic_route_payload
from app.events.schemas import GENERAL_MARKET_NEWS, OUT_OF_SCOPE
from app.model_gateway.service import DeterministicProvider, ModelGateway, ModelRequest
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository


def _document(title: str, content: str, *, tier: str = "S") -> Document:
    now = datetime.now(timezone.utc)
    return Document(
        id=new_id("doc"),
        source_id="src_1",
        source_tier=tier,
        external_id="ext_1",
        canonical_url="https://example.test/doc",
        title=title,
        content=content,
        content_hash="hash",
        published_at=now,
        ingested_at=now,
    )


def test_deterministic_route_accepts_mvp_hint() -> None:
    payload = deterministic_route_payload(
        {"rule_hint_type": "earnings_guidance", "rule_hint_confidence": 0.9}
    )
    assert payload["decision"] == "accept"
    assert payload["event_type"] == "earnings_guidance"
    assert "fact_checker" in payload["required_agents"]


def test_deterministic_route_rejects_general_news() -> None:
    payload = deterministic_route_payload({"rule_hint_type": GENERAL_MARKET_NEWS})
    assert payload["decision"] == "reject"
    assert payload["event_type"] == GENERAL_MARKET_NEWS


def test_router_accepts_earnings_announcement() -> None:
    repository = InMemoryRepository()
    router = EventRouter(ModelGateway(repository, DeterministicProvider()))
    document = _document(
        "示例公司（000001.SZ）2026年半年度业绩预告",
        "预计2026年半年度归母净利润同比增长20%至30%。",
    )
    hint = router.propose(document)
    decision = router.route(document, rule_hint=hint)
    merged = router.merge_classification(hint, decision)

    assert hint.event_type == "earnings_guidance"
    assert decision.decision == "accept"
    assert merged.event_type == "earnings_guidance"
    assert decision.required_agents


def test_router_rejects_general_market_news() -> None:
    repository = InMemoryRepository()
    router = EventRouter(ModelGateway(repository, DeterministicProvider()))
    document = _document(
        "油价应声走高",
        "国际油价跳涨，美股能源板块盘中走强，华尔街见闻快讯。",
        tier="A",
    )
    hint = router.propose(document)
    decision = router.route(document, rule_hint=hint)
    merged = router.merge_classification(hint, decision)

    assert hint.event_type == GENERAL_MARKET_NEWS
    assert decision.decision == "reject"
    assert merged.event_type == GENERAL_MARKET_NEWS


def test_pipeline_writes_router_audit_and_dormant_for_news() -> None:
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository)
    now = datetime.now(timezone.utc)
    result = pipeline.process(
        idempotency_key="router-news-1",
        source_id="src_news",
        source_tier="A",
        external_id="n1",
        url="https://example.test/n1",
        title="油价应声走高",
        content="国际油价跳涨，美股能源板块盘中走强，华尔街见闻快讯。",
        published_at=now,
    )
    assert result.event.event_type == GENERAL_MARKET_NEWS
    assert result.event.status == "dormant"
    audits = [
        item
        for item in repository.list_audit_logs()
        if item.action == "event.router_decision"
    ]
    assert len(audits) == 1
    assert audits[0].details["decision"] == "reject"


def test_pipeline_triages_accepted_mvp_event() -> None:
    repository = InMemoryRepository()
    pipeline = EventResearchPipeline(repository)
    now = datetime.now(timezone.utc)
    result = pipeline.process(
        idempotency_key="router-eg-1",
        source_id="src_s",
        source_tier="S",
        external_id="eg1",
        url="https://example.test/eg1",
        title="示例公司（000001.SZ）2026年半年度业绩预告",
        content="预计2026年半年度归母净利润同比增长20%至30%。",
        published_at=now,
    )
    assert result.event.event_type == "earnings_guidance"
    assert result.event.status in {"triaged", "needs_review"}
    assert repository.list_audit_logs()


def test_model_gateway_deterministic_event_route_operation() -> None:
    provider = DeterministicProvider()
    output = provider.invoke(
        ModelRequest(
            operation="event_route",
            input_schema_version="v1",
            output_schema_version="v1",
            payload={"rule_hint_type": OUT_OF_SCOPE},
            max_cost_usd=1,
        )
    )
    assert output["decision"] == "reject"
    assert output["event_type"] == OUT_OF_SCOPE
