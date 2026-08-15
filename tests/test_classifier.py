from datetime import datetime, timezone

from app.domain import Document
from app.events.classifier import EventClassifier, claim_template_for
from app.events.schemas import EVENT_SCHEMAS, SCHEMA_VERSION, schema_for_keywords


def make_document(title: str, content: str) -> Document:
    return Document(
        id="doc_test",
        source_id="szse",
        source_tier="S",
        external_id="test-1",
        canonical_url="https://example.test/1",
        title=title,
        content=content,
        content_hash="hash",
        published_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )


def test_earnings_guidance_extracts_period_and_change_rate() -> None:
    classifier = EventClassifier()
    document = make_document(
        "示例公司（000001.SZ）2026年半年度业绩预告",
        "公司预计2026年半年度净利润同比增长20%至30%。",
    )
    result = classifier.classify(document)

    assert result.event_type == "earnings_guidance"
    assert result.key_fields["period"] == "2026-半年度"
    change_rate = result.key_fields["change_rate"]
    assert change_rate["type"] == "range"
    assert change_rate["min"] == "20"
    assert change_rate["max"] == "30"
    assert change_rate["unit"] == "percent"
    assert result.schema_version == SCHEMA_VERSION


def test_earnings_guidance_extracts_profit_range_and_completes_required() -> None:
    classifier = EventClassifier()
    document = make_document(
        "示例公司2026年半年度业绩预告",
        "公司预计2026年半年度归属于上市公司股东的净利润16000万元至19000万元，同比增长20%至30%。",
    )
    result = classifier.classify(document)

    assert result.event_type == "earnings_guidance"
    profit_range = result.key_fields["range"]
    assert profit_range["type"] == "range"
    assert profit_range["min"] == "16000"
    assert profit_range["max"] == "19000"
    assert profit_range["unit"] == "万元"
    assert result.key_fields["profit_metric"] == "net_profit"
    # 必填齐全：period + profit_metric + range
    assert result.missing_required == []
    assert not result.needs_review
    assert result.confidence > 0.50


def test_missing_required_fields_triggers_needs_review() -> None:
    classifier = EventClassifier()
    # 只有同比变化，缺金额区间和明确利润指标
    document = make_document(
        "示例公司2026年半年度业绩预告",
        "公司预计2026年半年度净利润同比增长20%至30%。",
    )
    result = classifier.classify(document)

    assert result.event_type == "earnings_guidance"
    assert "profit_metric" in result.missing_required
    assert "range" in result.missing_required
    assert result.needs_review


def test_major_contract_extracts_amount() -> None:
    classifier = EventClassifier()
    document = make_document(
        "示例公司重大合同公告",
        "公司与甲方签订重大合同，合同金额为15000万元。",
    )
    result = classifier.classify(document)

    assert result.event_type == "major_contract"
    assert result.key_fields["amount"] == "15000"
    assert result.key_fields["currency"] == "CNY"
    # counterparties 缺失 -> needs_review
    assert "counterparties" in result.missing_required


def test_major_contract_extracts_named_counterparty() -> None:
    classifier = EventClassifier()
    document = make_document(
        "示例公司重大合同公告",
        "公司与中国移动通信集团有限公司签订重大合同，合同金额为15000万元，合同期限2年。",
    )
    result = classifier.classify(document)

    assert result.event_type == "major_contract"
    assert result.key_fields["counterparties"] == "中国移动通信集团有限公司"
    assert result.key_fields["duration"] == "2年"
    assert result.missing_required == []


def test_merger_acquisition_extracts_valuation() -> None:
    classifier = EventClassifier()
    document = make_document(
        "示例公司并购重组公告",
        "公司拟收购标的方股权，交易对价5亿元。",
    )
    result = classifier.classify(document)

    assert result.event_type == "merger_acquisition"
    assert result.key_fields["valuation"] == "5"
    assert "target" in result.missing_required


def test_merger_acquisition_extracts_named_target() -> None:
    classifier = EventClassifier()
    document = make_document(
        "示例公司并购重组公告",
        "公司拟收购星河科技有限公司的股权，交易对价5亿元，交易方式为现金收购。",
    )
    result = classifier.classify(document)

    assert result.event_type == "merger_acquisition"
    assert result.key_fields["target"] == "星河科技有限公司"
    assert result.key_fields["transaction_type"] == "acquisition"
    assert result.key_fields["stage"] == "planned"
    assert result.missing_required == []


def test_shareholder_reduction_extracts_ratio() -> None:
    classifier = EventClassifier()
    document = make_document(
        "示例公司股东减持公告",
        "控股股东拟减持不超过5%股份。",
    )
    result = classifier.classify(document)

    assert result.event_type == "shareholder_reduction"
    assert result.key_fields["ownership_ratio"] == "5"
    assert "shareholder" in result.missing_required


def test_shareholder_reduction_extracts_named_shareholder() -> None:
    classifier = EventClassifier()
    document = make_document(
        "示例公司股东减持公告",
        "控股股东李某某计划减持不超过5%股份，计划通过集中竞价方式减持。",
    )
    result = classifier.classify(document)

    assert result.event_type == "shareholder_reduction"
    assert result.key_fields["shareholder"] == "李某某"
    assert result.key_fields["stage"] == "planned"
    assert result.key_fields["ownership_ratio"] == "5"
    assert result.missing_required == []

def test_regulatory_penalty_extracts_authority() -> None:
    classifier = EventClassifier()
    document = make_document(
        "示例公司收到监管处罚",
        "公司近日收到证监会下发的行政处罚决定书。",
    )
    result = classifier.classify(document)

    assert result.event_type == "regulatory_penalty"
    assert result.key_fields["authority"] == "证监会"
    assert result.key_fields["penalty"] == "administrative"
    assert "subject" in result.missing_required


def test_general_market_news_when_finance_but_not_mvp_type() -> None:
    classifier = EventClassifier()
    document = make_document(
        "油价应声走高",
        "国际油价随即大幅跳涨，美股能源板块盘中走强，华尔街见闻快讯。",
    )
    result = classifier.classify(document)

    assert result.event_type == "general_market_news"
    assert result.confidence == 0.55
    assert not result.needs_review


def test_out_of_scope_when_no_keywords_match() -> None:
    classifier = EventClassifier()
    document = make_document("园区绿化通知", "下周对办公楼外围树木进行修剪与浇灌。")
    result = classifier.classify(document)

    assert result.event_type == "out_of_scope"
    assert result.confidence == 0.40
    assert not result.needs_review


def test_priority_avoids_misclassification_when_multiple_keywords_present() -> None:
    # 同时含"重组"和"合同"，应按优先级判定为 merger_acquisition
    classifier = EventClassifier()
    document = make_document(
        "示例公司并购重组公告",
        "公司拟通过重组方式收购标的，并签订相关合同，交易对价3亿元。",
    )
    result = classifier.classify(document)

    assert result.event_type == "merger_acquisition"


def test_claim_template_for_each_event_type() -> None:
    template = claim_template_for("earnings_guidance")
    assert template is not None
    assert template["predicate"] == "expects_net_profit_change"
    assert template["object_type"] == "range"
    assert "value" in template["conflict_rules"]

    assert claim_template_for("unsupported") is None
    assert claim_template_for("general_market_news") is None
    assert claim_template_for("out_of_scope") is None


def test_all_mvp_event_types_have_schemas() -> None:
    expected = {
        "earnings_guidance",
        "major_contract",
        "merger_acquisition",
        "shareholder_reduction",
        "regulatory_penalty",
        "macro_policy",
        "geopolitical_event",
    }
    assert expected.issubset(EVENT_SCHEMAS.keys())
    for schema in EVENT_SCHEMAS.values():
        assert schema.claim_predicate  # 每类都有受控谓词
        assert len(schema.required_fields) >= 2  # 每类至少 2 个必填


def test_schema_for_keywords_returns_none_when_unmatched() -> None:
    assert schema_for_keywords("一段不含任何事件关键词的文字") is None


def test_geopolitical_event_extracts_parties_region_and_action() -> None:
    classifier = EventClassifier()
    document = make_document(
        "美国对伊朗开战",
        "美国对伊朗开战，中东局势骤然升级，霍尔木兹海峡航运风险上升，原油价格跳涨。",
    )
    result = classifier.classify(document)

    assert result.event_type == "geopolitical_event"
    assert result.key_fields["parties"] == "美国/伊朗"
    assert result.key_fields["region"] == "中东"
    assert result.key_fields["action"] == "war"
    assert "原油" in result.key_fields["commodities"]
    assert result.missing_required == []
    assert not result.needs_review
    assert result.importance == 0.95


def test_geopolitical_event_missing_required_goes_needs_review() -> None:
    classifier = EventClassifier()
    document = make_document(
        "突发军事冲突",
        "凌晨发生导弹袭击，具体涉事方与地点尚待确认。",
    )
    result = classifier.classify(document)

    assert result.event_type == "geopolitical_event"
    assert "parties" in result.missing_required
    assert "region" in result.missing_required
    assert result.needs_review


def test_geopolitical_claim_template_uses_controlled_predicate() -> None:
    template = claim_template_for("geopolitical_event")
    assert template is not None
    assert template["predicate"] == "geopolitical_action"
    assert template["object_type"] == "string"
