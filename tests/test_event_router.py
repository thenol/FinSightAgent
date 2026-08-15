"""Event Router v2：相关性门控 + 开放分类（DD-21）。"""

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


class _StubProvider:
    """返回固定 v2 裁决的 stub，用于模拟真实 LLM。"""

    name = "stub"
    model = "stub-v1"
    estimated_cost_usd = 0.0

    def __init__(self, output: dict) -> None:
        self._output = output

    def invoke(self, request) -> dict:
        return dict(self._output)


def test_deterministic_route_relevant_for_mvp_hint() -> None:
    payload = deterministic_route_payload(
        {"rule_hint_type": "earnings_guidance", "rule_hint_confidence": 0.9}
    )
    assert payload["relevance"] == "relevant"
    assert payload["event_type"] == "earnings_guidance"
    assert "fact_checker" in payload["required_agents"]


def test_deterministic_route_unsure_for_general_news() -> None:
    payload = deterministic_route_payload({"rule_hint_type": GENERAL_MARKET_NEWS})
    assert payload["relevance"] == "unsure"
    assert payload["event_type"] == GENERAL_MARKET_NEWS


def test_deterministic_route_irrelevant_for_out_of_scope() -> None:
    payload = deterministic_route_payload({"rule_hint_type": OUT_OF_SCOPE})
    assert payload["relevance"] == "irrelevant"
    assert payload["event_type"] == OUT_OF_SCOPE


def test_router_relevant_for_earnings_announcement() -> None:
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
    assert decision.relevance == "relevant"
    assert merged.event_type == "earnings_guidance"
    assert decision.required_agents


def test_router_unsure_for_general_market_news() -> None:
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
    assert decision.relevance == "unsure"
    assert merged.event_type == GENERAL_MARKET_NEWS


def test_router_accepts_candidate_type_from_llm() -> None:
    """LLM 开放分类：白名单外的重大事件以候选类型落库（DD-21 §2.4）。"""
    repository = InMemoryRepository()
    stub = _StubProvider(
        {
            "relevance": "relevant",
            "event_type": "geopolitical_crisis",
            "importance": 0.9,
            "confidence": 0.8,
            "required_agents": ["fact_checker", "impact_analyst"],
            "reason": "地缘冲突影响原油供应",
        }
    )
    router = EventRouter(ModelGateway(repository, stub))
    document = _document(
        "欧洲遭遇极端高温",
        "连续多日极端高温天气导致电网负荷创纪录，农业减产风险上升。",
        tier="A",
    )
    hint = router.propose(document)
    decision = router.route(document, rule_hint=hint)
    merged = router.merge_classification(hint, decision)

    assert hint.event_type == OUT_OF_SCOPE  # 规则词表未命中
    assert decision.relevance == "relevant"
    assert decision.is_candidate_type is True
    assert merged.event_type == "geopolitical_crisis"
    assert merged.importance == 0.9
    assert "candidate_type_confirmation" in merged.missing_required
    assert merged.needs_review is True


def test_router_downgrades_invalid_candidate_label() -> None:
    """LLM 判 relevant 但类型标签非法（保留字/非 snake_case）→ 降级 unsure。"""
    repository = InMemoryRepository()
    stub = _StubProvider(
        {
            "relevance": "relevant",
            "event_type": "General Market News!",  # 非法标签
            "importance": 0.6,
            "confidence": 0.7,
            "required_agents": [],
            "reason": "bad label",
        }
    )
    router = EventRouter(ModelGateway(repository, stub))
    document = _document("某条财经快讯", "内容提及股价波动与基金调仓。")
    hint = router.propose(document)
    decision = router.route(document, rule_hint=hint)

    assert decision.relevance == "unsure"
    assert decision.is_candidate_type is False


def test_router_irrelevant_archives_event() -> None:
    """LLM 判 irrelevant：事件归档为 out_of_scope。"""
    repository = InMemoryRepository()
    stub = _StubProvider(
        {
            "relevance": "irrelevant",
            "event_type": "sports_news",
            "importance": 0.05,
            "confidence": 0.9,
            "required_agents": [],
            "reason": "体育赛事与经济无关",
        }
    )
    router = EventRouter(ModelGateway(repository, stub))
    document = _document("某球队赢得联赛冠军", "昨晚决赛落幕，球迷庆祝。")
    hint = router.propose(document)
    decision = router.route(document, rule_hint=hint)
    merged = router.merge_classification(hint, decision)

    assert decision.relevance == "irrelevant"
    assert merged.event_type == OUT_OF_SCOPE
    assert merged.needs_review is False


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
    assert audits[0].details["relevance"] == "unsure"
    assert audits[0].details["router_schema_version"] == "v2"


def test_pipeline_triages_relevant_mvp_event() -> None:
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


def test_pipeline_candidate_type_end_to_end() -> None:
    """候选类型事件端到端：落库 needs_review、重要度取 Router 建议值、Claim 不崩溃。"""
    repository = InMemoryRepository()
    stub = _StubProvider(
        {
            "relevance": "relevant",
            "event_type": "geopolitical_crisis",
            "importance": 0.9,
            "confidence": 0.8,
            "required_agents": ["fact_checker"],
            "reason": "地缘冲突",
        }
    )
    from app.events.router import EventRouter as _EventRouter

    pipeline = EventResearchPipeline(
        repository,
        event_router=_EventRouter(ModelGateway(repository, stub)),
    )
    now = datetime.now(timezone.utc)
    result = pipeline.process(
        idempotency_key="router-cand-1",
        source_id="src_geo",
        source_tier="S",
        external_id="g1",
        url="https://example.test/g1",
        title="欧洲遭遇极端高温",
        content="连续多日极端高温天气导致电网负荷创纪录，农业减产风险上升。",
        published_at=now,
    )
    assert result.event.event_type == "geopolitical_crisis"
    assert result.event.status == "needs_review"
    assert "candidate_type_confirmation" in result.event.missing_required
    # 候选类型无规则基线，Router 建议 0.9 作为类型基线分量（0.30 权重），应显著高于归档线
    assert result.event.importance > 0.3
    assert result.claim is not None  # legacy 谓词回退，不崩溃


def test_model_gateway_deterministic_event_route_operation() -> None:
    provider = DeterministicProvider()
    output = provider.invoke(
        ModelRequest(
            operation="event_route",
            input_schema_version="v2",
            output_schema_version="v2",
            payload={"rule_hint_type": OUT_OF_SCOPE},
            max_cost_usd=1,
        )
    )
    assert output["relevance"] == "irrelevant"
    assert output["event_type"] == OUT_OF_SCOPE
