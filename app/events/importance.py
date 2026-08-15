"""重要度与紧急度计算。

ImportanceCalculator 使用可解释的确定性特征分（DD-20 §8、IMP-023），作为
Router 的输入。特征包括：来源等级、事件类型基线、金额/业绩变化相对规模、
监管严重度和时效性。Router 可以在受限范围内调整分值，但必须返回理由和规则版本。

重要度范围 [0, 1]；紧急度为 low | normal | high | critical。低重要度归档阈值
配置化，不写死在提示词中。分项保存以支持可解释性。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from app.events.schemas import EVENT_SCHEMAS, GENERAL_MARKET_NEWS, OUT_OF_SCOPE

IMPORTANCE_RULE_VERSION = "importance-v1"
ARCHIVE_THRESHOLD = 0.30


@dataclass(frozen=True)
class ImportanceScore:
    importance: float
    urgency: str
    components: dict[str, float]
    rule_version: str = IMPORTANCE_RULE_VERSION


class ImportanceCalculator:
    """按确定性特征计算事件重要度。"""

    def calculate(
        self,
        *,
        event_type: str,
        source_tier: str,
        key_fields: dict[str, Any],
        published_at: datetime,
        now: Optional[datetime] = None,
        type_baseline_override: Optional[float] = None,
    ) -> ImportanceScore:
        # 候选类型（Router LLM 开放分类产出）无规则基线，由 Router 建议值替代（DD-21 §2.6）
        type_baseline = (
            min(1.0, max(0.0, type_baseline_override))
            if type_baseline_override is not None
            else self._type_baseline(event_type)
        )
        tier_score = self._tier_score(source_tier)
        magnitude_score = self._magnitude_score(event_type, key_fields)
        severity_score = self._severity_score(event_type, key_fields)
        recency_score = self._recency_score(published_at, now)

        # 加权合成，各分量范围 [0,1]
        importance = (
            0.30 * tier_score
            + 0.30 * type_baseline
            + 0.20 * magnitude_score
            + 0.10 * severity_score
            + 0.10 * recency_score
        )
        importance = round(min(1.0, max(0.0, importance)), 3)
        urgency = self._urgency(importance, event_type)

        return ImportanceScore(
            importance=importance,
            urgency=urgency,
            components={
                "tier_score": tier_score,
                "type_baseline": type_baseline,
                "magnitude_score": magnitude_score,
                "severity_score": severity_score,
                "recency_score": recency_score,
            },
        )

    def _type_baseline(self, event_type: str) -> float:
        schema = EVENT_SCHEMAS.get(event_type)
        if schema is not None:
            return schema.importance
        if event_type == GENERAL_MARKET_NEWS:
            return 0.35
        if event_type == OUT_OF_SCOPE:
            return 0.10
        # 历史 unsupported 等
        return 0.10

    def _tier_score(self, source_tier: str) -> float:
        return {"S": 1.0, "A": 0.8, "B": 0.5, "C": 0.3}.get(source_tier, 0.3)

    def _magnitude_score(self, event_type: str, key_fields: dict[str, Any]) -> float:
        """金额或业绩变化相对规模。"""
        if event_type == "earnings_guidance":
            change_rate = key_fields.get("change_rate")
            if isinstance(change_rate, dict):
                max_change = self._to_float(change_rate.get("max"))
                if max_change is not None:
                    return min(1.0, abs(max_change) / 100.0)
        if event_type in {"major_contract", "merger_acquisition"}:
            amount = self._to_float(key_fields.get("amount") or key_fields.get("valuation"))
            if amount is not None:
                # 亿元量级映射：10亿+ -> 1.0
                return min(1.0, amount / 10.0)
        if event_type == "shareholder_reduction":
            ratio = self._to_float(key_fields.get("ownership_ratio"))
            if ratio is not None:
                return min(1.0, ratio / 5.0)
        return 0.50

    def _severity_score(self, event_type: str, key_fields: dict[str, Any]) -> float:
        if event_type == "regulatory_penalty":
            penalty = key_fields.get("penalty")
            if penalty == "administrative":
                return 1.0
            if penalty:
                return 0.80
            return 0.60
        return 0.50

    def _recency_score(self, published_at: datetime, now: Optional[datetime]) -> float:
        if not published_at:
            return 0.50
        current = now or datetime.now(timezone.utc)
        delta = abs((current - self._to_utc(published_at)).total_seconds())
        days = delta / 86400
        if days <= 1:
            return 1.0
        if days >= 30:
            return 0.20
        return round(1.0 - days / 30 * 0.80, 4)

    def _urgency(self, importance: float, event_type: str) -> str:
        if event_type == "regulatory_penalty" and importance >= 0.80:
            return "critical"
        if importance >= 0.85:
            return "high"
        if importance >= 0.55:
            return "normal"
        return "low"

    def _to_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(Decimal(str(value)))
        except Exception:  # noqa: BLE001
            return None

    def _to_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
