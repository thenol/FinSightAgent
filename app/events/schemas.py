"""五类 MVP 事件专用 Schema。

每类事件定义其 ``key_fields``（字段名单）、必填字段、Claim 模板谓词与冲突规则
（DD-20 §4、IMP-021）。事件分类器抽取 key_fields 后，按 Schema 校验：
缺失必填字段时事件进入 ``needs_review``，不强制选择最接近类型。

数字以字符串十进制传输，比例使用小数（DD-00 §3）。Claim 模板谓词来自
``app.evidence.predicates`` 受控词表，使事件抽取与事实核验共享同一谓词空间。
"""

from dataclasses import dataclass, field
from typing import Optional

SCHEMA_VERSION = "event-schema-v1"

# 宏观政策类事件独立关键词，避免与公司事件混淆
GEOPOLITICAL_KEYWORDS = (
    "开战",
    "宣战",
    "战争",
    "军事打击",
    "空袭",
    "导弹袭击",
    "制裁",
    "军演",
    "军事演习",
    "政变",
    "恐怖袭击",
    "入侵",
    "武装冲突",
)

MACRO_POLICY_KEYWORDS = (
    "加息",
    "降息",
    "利率",
    "FOMC",
    "美联储",
    "央行",
    "人民银行",
    "欧洲央行",
    "rate hike",
    "rate cut",
    "interest rate",
    "LPR",
    "存款准备金率",
    "货币政策",
    "利率决议",
    "联邦基金利率",
    "基准利率",
)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    value_type: str  # string | decimal | range | date
    required: bool = False
    description: str = ""


@dataclass(frozen=True)
class ClaimTemplate:
    """由一个主 key_field 驱动的类型化业务 Claim。"""

    key_field: str
    predicate: str
    object_type: str


@dataclass(frozen=True)
class EventSchema:
    event_type: str
    keywords: tuple[str, ...]
    importance: float
    fields: tuple[FieldSpec, ...]
    claim_predicate: str
    claim_object_type: str
    claim_templates: tuple[ClaimTemplate, ...] = field(default_factory=tuple)
    conflict_rules: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields if spec.required)


EVENT_SCHEMAS: dict[str, EventSchema] = {
    "geopolitical_event": EventSchema(
        event_type="geopolitical_event",
        keywords=GEOPOLITICAL_KEYWORDS,
        importance=0.95,
        fields=(
            FieldSpec("parties", "string", required=True, description="涉及方，如 美国/伊朗"),
            FieldSpec("region", "string", required=True, description="事发地区，如 中东"),
            FieldSpec("action", "string", description="行动类型，如 war/sanction/strike"),
            FieldSpec("commodities", "string", description="关联商品，如 原油/黄金"),
        ),
        claim_predicate="geopolitical_action",
        claim_object_type="string",
        claim_templates=(
            ClaimTemplate("action", "geopolitical_action", "string"),
        ),
        conflict_rules=("subject", "scope", "semantic"),
    ),
    "macro_policy": EventSchema(
        event_type="macro_policy",
        keywords=MACRO_POLICY_KEYWORDS,
        importance=0.92,
        fields=(
            FieldSpec(
                "policy_body", "string", required=True, description="政策主体，如 美联储/PBOC"
            ),
            FieldSpec(
                "rate_decision", "string", required=True, description="利率决策，如 加息/降息/维持"
            ),
            FieldSpec("rate_change_bp", "decimal", description="基点变化幅度，如 25"),
            FieldSpec("target_rate", "string", description="目标利率区间，如 5.25%-5.50%"),
            FieldSpec("effective_date", "date", description="生效日期"),
        ),
        claim_predicate="adjusts_policy_rate",
        claim_object_type="string",
        claim_templates=(
            ClaimTemplate("rate_decision", "adjusts_policy_rate", "string"),
        ),
        conflict_rules=("value", "scope", "unit"),
    ),
    "earnings_guidance": EventSchema(
        event_type="earnings_guidance",
        keywords=("业绩预告", "预计净利润", "业绩快报", "预计报告期"),
        importance=0.80,
        fields=(
            FieldSpec("period", "string", required=True, description="业绩期间，如 2026-H1"),
            FieldSpec(
                "profit_metric", "string", required=True, description="利润指标，如 归母净利润"
            ),
            FieldSpec("range", "range", required=True, description="预计金额区间"),
            FieldSpec("change_rate", "range", description="同比变化幅度区间"),
        ),
        claim_predicate="expects_net_profit_change",
        claim_object_type="range",
        claim_templates=(
            ClaimTemplate("range", "expects_net_profit", "range"),
            ClaimTemplate("change_rate", "expects_net_profit_change", "range"),
        ),
        conflict_rules=("value", "period", "scope"),
    ),
    "major_contract": EventSchema(
        event_type="major_contract",
        keywords=("重大合同", "中标", "合同公告", "签订合同"),
        importance=0.75,
        fields=(
            FieldSpec("counterparties", "string", required=True, description="合同对手方"),
            FieldSpec("amount", "decimal", required=True, description="合同金额"),
            FieldSpec("currency", "string", required=True, description="币种"),
            FieldSpec("duration", "string", description="合同期限"),
        ),
        claim_predicate="signs_major_contract",
        claim_object_type="decimal",
        claim_templates=(ClaimTemplate("amount", "signs_major_contract", "decimal"),),
        conflict_rules=("value", "subject", "unit"),
    ),
    "merger_acquisition": EventSchema(
        event_type="merger_acquisition",
        keywords=("并购", "重组", "收购", "吸收合并"),
        importance=0.85,
        fields=(
            FieldSpec("target", "string", required=True, description="标的方"),
            FieldSpec("transaction_type", "string", required=True, description="交易方式"),
            FieldSpec("stage", "string", required=True, description="阶段"),
            FieldSpec("valuation", "decimal", description="交易对价"),
        ),
        claim_predicate="announces_merger_acquisition",
        claim_object_type="entity",
        claim_templates=(
            ClaimTemplate("target", "announces_merger_acquisition", "entity"),
        ),
        conflict_rules=("subject", "value", "scope"),
    ),
    "shareholder_reduction": EventSchema(
        event_type="shareholder_reduction",
        keywords=("减持", "股份减持", "拟减持", "集中竞价减持"),
        importance=0.80,
        fields=(
            FieldSpec("shareholder", "string", required=True, description="减持股东"),
            FieldSpec("stage", "string", required=True, description="计划/完成"),
            FieldSpec("shares", "decimal", description="减持股份数量"),
            FieldSpec("ownership_ratio", "decimal", description="减持比例"),
        ),
        claim_predicate="reduces_holding",
        claim_object_type="decimal",
        claim_templates=(
            ClaimTemplate("shares", "reduces_holding", "decimal"),
            ClaimTemplate("ownership_ratio", "reduces_holding_ratio", "decimal"),
        ),
        conflict_rules=("value", "subject", "unit"),
    ),
    "regulatory_penalty": EventSchema(
        event_type="regulatory_penalty",
        keywords=("处罚", "监管措施", "行政处罚", "立案调查", "警示函"),
        importance=0.90,
        fields=(
            FieldSpec("authority", "string", required=True, description="监管主体"),
            FieldSpec("subject", "string", required=True, description="处罚对象"),
            FieldSpec("reason", "string", required=True, description="处罚原因"),
            FieldSpec("penalty", "string", description="处罚措施"),
        ),
        claim_predicate="penalized_by_regulator",
        claim_object_type="string",
        claim_templates=(
            ClaimTemplate("penalty", "penalized_by_regulator", "string"),
        ),
        conflict_rules=("subject", "scope", "semantic"),
    ),
}

# 按优先级排序：宏观政策 > 地缘事件 > 监管处罚 > 并购重组 > 减持 > 重大合同 > 业绩预告
# （避免一篇同时含"重组"和"合同"的公告被误判为低优先级类型）
EVENT_TYPE_PRIORITY = (
    "macro_policy",
    "geopolitical_event",
    "regulatory_penalty",
    "merger_acquisition",
    "shareholder_reduction",
    "major_contract",
    "earnings_guidance",
)

# MVP 研究主路径五类（可开 Agent 工作流的候选）
MVP_EVENT_TYPES = frozenset(EVENT_TYPE_PRIORITY)

# 非五类桶：综合财经资讯 vs 范围外（历史 unsupported 视为 out_of_scope）
GENERAL_MARKET_NEWS = "general_market_news"
OUT_OF_SCOPE = "out_of_scope"
LEGACY_UNSUPPORTED = "unsupported"
NON_MVP_EVENT_TYPES = frozenset(
    {GENERAL_MARKET_NEWS, OUT_OF_SCOPE, LEGACY_UNSUPPORTED}
)

# 未命中五类、但明显属财经资讯时的弱特征（不升格为五类）
FINANCE_HINT_KEYWORDS = (
    "股市",
    "A股",
    "港股",
    "美股",
    "债券",
    "原油",
    "黄金",
    "外汇",
    "央行",
    "美联储",
    "财报",
    "营收",
    "电话会",
    "投资者",
    "指数",
    "期货",
    "基金",
    "IPO",
    "宏观",
    "通胀",
    "利率",
    "美元",
    "人民币",
    "华尔街",
    "见闻",
    "快讯",
    "盘中",
    "收盘",
    "开盘",
    "净买入",
    "持仓",
    "股价",
    "涨跌",
    "市值",
    "研报",
    "券商",
    "投行",
    "融资",
    "流动性",
    "供应链",
    "产能",
    "订单",
    "净利润",
    "同比",
    "环比",
)


def get_schema(event_type: str) -> Optional[EventSchema]:
    return EVENT_SCHEMAS.get(event_type)


def schema_for_keywords(text: str) -> Optional[EventSchema]:
    """按优先级返回首个关键词命中的事件 Schema。"""
    for event_type in EVENT_TYPE_PRIORITY:
        schema = EVENT_SCHEMAS[event_type]
        if any(keyword in text for keyword in schema.keywords):
            return schema
    return None


def is_mvp_event_type(event_type: str) -> bool:
    return event_type in MVP_EVENT_TYPES


def is_non_mvp_event_type(event_type: str) -> bool:
    return event_type in NON_MVP_EVENT_TYPES


def is_candidate_event_type(event_type: str) -> bool:
    """候选类型：Router LLM 开放分类产出的一等词表外标签（DD-21 §2.4）。"""
    return (
        bool(event_type)
        and not is_mvp_event_type(event_type)
        and not is_non_mvp_event_type(event_type)
    )


def looks_like_finance_news(text: str) -> bool:
    return any(keyword in text for keyword in FINANCE_HINT_KEYWORDS)


def fallback_event_type(text: str) -> str:
    """五类未命中时的分层结果：综合资讯或范围外。"""
    if looks_like_finance_news(text):
        return GENERAL_MARKET_NEWS
    return OUT_OF_SCOPE