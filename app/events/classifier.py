"""事件分类器与 key_fields 抽取。

EventClassifier 在一等事件类型（七类 MVP 词表）中分类并抽取结构化 ``key_fields``
（DD-20 §4、DD-21、IMP-021）。抽取使用确定性正则，不依赖模型；关键数字走规则，
避免模型心算。分类结果只作为 Router 的规则 hint，门控由 Router v2 相关性裁决承担。

分类输出包含：事件类型、重要度、置信度、key_fields、缺失的必填字段。
- 必填字段齐全 -> triaged。
- 缺失必填字段 -> needs_review（不阻塞，进入审核补字段）。
- 未命中一等类型但像财经资讯 -> general_market_news（规则 hint，由 Router 裁决）。
- 非财经/无法处理 -> out_of_scope（规则 hint，由 Router 裁决）。

置信度初始由命中关键词数与已抽取字段比例决定，需用标注集校准（IMP-021 完成条件）。
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from app.domain import Document
from app.events.schemas import (
    GENERAL_MARKET_NEWS,
    OUT_OF_SCOPE,
    EventSchema,
    fallback_event_type,
    get_schema,
    is_non_mvp_event_type,
    schema_for_keywords,
)

# 金额：支持"X万元"、"X亿元"、"X万"、"X亿"、"X元"，含千分位
_AMOUNT = re.compile(r"(\d[\d,]*\.?\d*)\s*(亿元|万元|亿|万|元)")
# 同比变化幅度："同比增长20%至30%"、"增幅10%-20%"、"下降5%至8%"
_CHANGE_RATE = re.compile(
    r"(?:同比增长|同比下降|增幅|降幅|增长|下降)\s*(\d+\.?\d*)\s*%?\s*(?:至|[-~])\s*(\d+\.?\d*)\s*%"
)
# 业绩区间金额："净利润X万元至Y万元" 或 "X亿元至Y亿元"
_PROFIT_RANGE = re.compile(
    r"(\d[\d,]*\.?\d*)\s*(亿元|万元|亿|万)\s*(?:至|[-~])\s*(\d[\d,]*\.?\d*)\s*(亿元|万元|亿|万)?"
)
# 期间：2026年半年度、2026年一季度、2026-H1
_PERIOD = re.compile(
    r"(\d{4})\s*年\s*第?\s*(半年度|一季度|二季度|三季度|四季度|年度|前三季度|中期)"
)
# 比例：减持比例"不超过5%"、"计划减持6%"
_RATIO = re.compile(r"(?:不超过|拟减持|减持|比例|占[^，。；]{0,20}?)\s*(\d+\.?\d*)\s*%")
# 日期：YYYY年MM月DD日 或 YYYY-MM-DD
_DATE = re.compile(r"(\d{4})\s*[-年]\s*(\d{1,2})\s*[-月]\s*(\d{1,2})\s*日?")
_COUNTERPARTY = re.compile(
    r"与(?P<value>[^，。；]{2,50}?)(?:签订|订立|达成)(?:了)?[^，。；]{0,20}?合同"
)
_DURATION = re.compile(r"(?:合同|履行)(?:期限|期)\s*(?:为|：|:)?\s*(?P<value>[^，。；]{2,40})")
_TARGET = re.compile(
    r"(?:收购|购买)(?P<value>[\u4e00-\u9fffA-Za-z0-9（）()·]{2,50}?)(?:的)?(?:股权|股份|资产|业务)"
)
_SHAREHOLDER = re.compile(
    r"(?:股东|控股股东|实际控制人)\s*(?P<value>[\u4e00-\u9fffA-Za-z0-9（）()·]{2,40}?)(?:计划|拟|通过|将)?减持"
)
_SHARES = re.compile(r"(?:不超过|拟减持|减持)\s*(\d[\d,]*\.?\d*)\s*(万股|亿股|股)")
_PENALTY_SUBJECT = re.compile(
    r"(?P<value>[^，。；]{2,50}?)(?:收到|被处以|受到)(?:行政)?(?:处罚|监管措施|警示)"
)
_PENALTY_REASON = re.compile(r"(?:因|由于)(?P<value>[^，。；]{2,80}?)(?:，|。|；|被|受到)")


@dataclass(frozen=True)
class ClassificationResult:
    event_type: str
    importance: float
    confidence: float
    key_fields: dict[str, Any] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    schema_version: str = ""

    @property
    def needs_review(self) -> bool:
        return bool(self.missing_required) and not is_non_mvp_event_type(self.event_type)


class EventClassifier:
    """在一等事件类型中分类并抽取 key_fields；未命中则产出分层 hint 供 Router 裁决。"""

    def classify(self, document: Document) -> ClassificationResult:
        text = f"{document.title}\n{document.content}"
        schema = schema_for_keywords(text)
        if schema is None:
            event_type = fallback_event_type(text)
            if event_type == GENERAL_MARKET_NEWS:
                return ClassificationResult(
                    event_type=GENERAL_MARKET_NEWS,
                    importance=0.35,
                    confidence=0.55,
                    schema_version="",
                )
            return ClassificationResult(
                event_type=OUT_OF_SCOPE,
                importance=0.15,
                confidence=0.40,
                schema_version="",
            )
        key_fields = self._extract_fields(schema, text)
        missing = self._missing_required(schema, key_fields)
        confidence = self._confidence(schema, key_fields, missing)

        return ClassificationResult(
            event_type=schema.event_type,
            importance=schema.importance,
            confidence=confidence,
            key_fields=key_fields,
            missing_required=missing,
            schema_version=schema.schema_version,
        )

    def _extract_fields(self, schema: EventSchema, text: str) -> dict[str, Any]:
        if schema.event_type == "macro_policy":
            return self._extract_macro_policy(text)
        if schema.event_type == "geopolitical_event":
            return self._extract_geopolitical(text)
        if schema.event_type == "earnings_guidance":
            return self._extract_earnings_guidance(text)
        if schema.event_type == "major_contract":
            return self._extract_major_contract(text)
        if schema.event_type == "merger_acquisition":
            return self._extract_merger_acquisition(text)
        if schema.event_type == "shareholder_reduction":
            return self._extract_shareholder_reduction(text)
        if schema.event_type == "regulatory_penalty":
            return self._extract_regulatory_penalty(text)
        return {}

    def _extract_macro_policy(self, text: str) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        # 优先匹配更具体的机构名称，避免“央行”被误匹配到所有含央行字样的文本。
        policy_bodies = (
            ("Federal Reserve", "federal_reserve"),
            ("美联储", "federal_reserve"),
            ("FOMC", "federal_reserve"),
            ("中国人民银行", "pboc"),
            ("人民银行", "pboc"),
            ("欧洲央行", "ecb"),
            ("ECB", "ecb"),
            ("英格兰银行", "boe"),
            ("央行", "pboc"),
        )
        for name, code in policy_bodies:
            if name in text:
                fields["policy_body"] = code
                break

        if "加息" in text or "rate hike" in text.lower():
            fields["rate_decision"] = "hike"
        elif "降息" in text or "rate cut" in text.lower():
            fields["rate_decision"] = "cut"
        elif (
            "维持" in text
            or "不变" in text
            or "hold" in text.lower()
            or "unchanged" in text.lower()
        ):
            fields["rate_decision"] = "hold"

        bp_match = re.search(r"(\d+\.?\d*)\s*(个基点|bp|BPS)", text, re.IGNORECASE)
        if bp_match:
            fields["rate_change_bp"] = self._decimal(bp_match.group(1))

        target_rate = re.search(r"(\d+\.?\d*)%\s*[-~至]\s*(\d+\.?\d*)%", text)
        if target_rate:
            fields["target_rate"] = f"{target_rate.group(1)}%-{target_rate.group(2)}%"

        date_match = _DATE.search(text)
        if date_match:
            year, month, day = date_match.group(1), date_match.group(2), date_match.group(3)
            fields["effective_date"] = f"{year}-{month.zfill(2)}-{day.zfill(2)}"

        return fields

    def _extract_geopolitical(self, text: str) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        # 涉及方："X对Y开战/实施制裁"、"X与Y爆发武装冲突"
        parties = re.search(
            r"(?P<a>[一-龥]{2,10}?)(?:对|与|向|和)(?P<b>[一-龥]{2,10}?)"
            r"(?:开战|宣战|发动战争|实施制裁|发起制裁|军事打击|发动空袭|爆发武装冲突|举行军演)",
            text,
        )
        if parties:
            fields["parties"] = f"{parties.group('a')}/{parties.group('b')}"

        regions = ("中东", "波斯湾", "红海", "欧洲", "东欧", "亚太", "南海", "台海", "非洲", "拉美")
        for region in regions:
            if region in text:
                fields["region"] = region
                break

        for action, words in {
            "war": ("开战", "宣战", "发动战争", "入侵"),
            "strike": ("军事打击", "空袭", "导弹袭击", "恐怖袭击"),
            "sanction": ("制裁",),
            "military_exercise": ("军演", "军事演习"),
            "coup": ("政变",),
        }.items():
            if any(word in text for word in words):
                fields["action"] = action
                break

        commodities = [name for name in ("原油", "石油", "黄金", "天然气", "航运") if name in text]
        if commodities:
            fields["commodities"] = "/".join(commodities)

        return fields

    def _extract_earnings_guidance(self, text: str) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        period = _PERIOD.search(text)
        if period:
            fields["period"] = f"{period.group(1)}-{period.group(2)}"

        change = _CHANGE_RATE.search(text)
        if change:
            fields["change_rate"] = {
                "type": "range",
                "min": self._decimal(change.group(1)),
                "max": self._decimal(change.group(2)),
                "unit": "percent",
            }

        profit = _PROFIT_RANGE.search(text)
        if profit:
            unit = profit.group(2)
            fields["range"] = {
                "type": "range",
                "min": self._decimal(profit.group(1)),
                "max": self._decimal(profit.group(3)),
                "unit": unit,
            }
            fields["profit_metric"] = "net_profit"
            fields["currency"] = "CNY"

        return fields

    def _extract_major_contract(self, text: str) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        amount = _AMOUNT.search(text)
        if amount:
            fields["amount"] = self._decimal(amount.group(1))
            unit = amount.group(2)
            fields["currency"] = "CNY"
            fields["unit"] = unit
        counterparty = _COUNTERPARTY.search(text)
        if counterparty and counterparty.group("value").strip() not in {"甲方", "乙方", "对方"}:
            fields["counterparties"] = counterparty.group("value").strip()
        duration = _DURATION.search(text)
        if duration:
            fields["duration"] = duration.group("value").strip()
        return fields

    def _extract_merger_acquisition(self, text: str) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        target = _TARGET.search(text)
        if target and target.group("value").strip() not in {"标的", "标的方", "目标公司"}:
            fields["target"] = target.group("value").strip()
        for transaction_type, words in {
            "acquisition": ("收购", "购买"),
            "merger": ("吸收合并", "合并"),
            "restructuring": ("重组",),
        }.items():
            if any(word in text for word in words):
                fields["transaction_type"] = transaction_type
                break
        for stage, words in {
            "planned": ("拟", "计划", "筹划"),
            "signed": ("签署", "签订"),
            "completed": ("完成", "已收购"),
            "terminated": ("终止", "停止"),
        }.items():
            if any(word in text for word in words):
                fields["stage"] = stage
                break
        amount = _AMOUNT.search(text)
        if amount:
            fields["valuation"] = self._decimal(amount.group(1))
            fields["unit"] = amount.group(2)
        return fields

    def _extract_shareholder_reduction(self, text: str) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        ratio = _RATIO.search(text)
        if ratio:
            fields["ownership_ratio"] = self._decimal(ratio.group(1))
        shareholder = _SHAREHOLDER.search(text)
        if shareholder:
            fields["shareholder"] = shareholder.group("value").strip()
        if any(word in text for word in ("完成减持", "减持完成")):
            fields["stage"] = "completed"
        elif any(word in text for word in ("拟减持", "计划减持", "减持计划")):
            fields["stage"] = "planned"
        shares = _SHARES.search(text)
        if shares:
            fields["shares"] = self._decimal(shares.group(1))
            fields["unit"] = shares.group(2)
        return fields

    def _extract_regulatory_penalty(self, text: str) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        # 监管主体识别（简化：常见监管机构关键词）
        authorities = (
            "中国证监会",
            "证监会",
            "证监局",
            "证券交易所",
            "交易所",
            "市场监督管理局",
            "金融监管局",
            "监管局",
        )
        for authority in authorities:
            if authority in text:
                fields["authority"] = authority
                break
        subject = _PENALTY_SUBJECT.search(text)
        if subject:
            subject_value = subject.group("value").strip()
            if subject_value not in {"公司", "本公司", "公司近日", "本公司近日"}:
                fields["subject"] = subject_value
        reason = _PENALTY_REASON.search(text)
        if reason:
            fields["reason"] = reason.group("value").strip()
        for penalty, words in {
            "administrative": ("行政处罚", "罚款"),
            "warning_letter": ("警示函", "警示"),
            "regulatory_measure": ("监管措施", "责令改正"),
            "investigation": ("立案调查",),
        }.items():
            if any(word in text for word in words):
                fields["penalty"] = penalty
                break
        return fields

    def _missing_required(self, schema: EventSchema, key_fields: dict[str, Any]) -> list[str]:
        return [
            field_name
            for field_name in schema.required_fields
            if field_name not in key_fields or key_fields[field_name] in (None, "", {}, [])
        ]

    def _confidence(
        self,
        schema: EventSchema,
        key_fields: dict[str, Any],
        missing: list[str],
    ) -> float:
        total = len(schema.fields)
        if total == 0:
            return 0.50
        filled = total - len(missing)
        # 命中关键词 + 已抽取字段比例
        base = 0.50
        field_ratio = filled / total
        return round(min(1.0, base + field_ratio * 0.45), 3)

    def _decimal(self, value: str) -> str:
        try:
            return format(Decimal(value.replace(",", "")), "f")
        except (InvalidOperation, ValueError, AttributeError):
            return value


def claim_template_for(event_type: str) -> Optional[dict[str, Any]]:
    """返回某类事件的 Claim 模板（谓词与 object 类型），供事实核验填充。"""
    schema = get_schema(event_type)
    if schema is None:
        return None
    return {
        "predicate": schema.claim_predicate,
        "object_type": schema.claim_object_type,
        "conflict_rules": list(schema.conflict_rules),
    }
