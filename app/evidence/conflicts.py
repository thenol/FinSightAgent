"""事实冲突检测。

ConflictDetector 比较同主体、同谓词、同期 Claim 的值、单位、口径与语义，
生成 ``Conflict``（DD-40 §8）。冲突类型：value/unit/period/subject/time/
scope/semantic；严重度：critical/major/minor。

严重度判定：
- critical：改变核心金额、事件主体或监管结论。
- major：影响核心事实但可能由口径解释，阻止完整研究报告。
- minor：不改变主要结论，保留说明后继续。
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from app.evidence.claims import NormalizedClaim

CONFLICT_TYPES = (
    "value",
    "unit",
    "period",
    "subject",
    "time",
    "scope",
    "semantic",
)


@dataclass(frozen=True)
class Conflict:
    conflict_type: str
    severity: str
    summary: str
    claim_ids: list[str] = field(default_factory=list)


class ConflictDetector:
    """比较两个规范化 Claim，返回第一个识别到的冲突（若有）。"""

    def detect(
        self,
        left: NormalizedClaim,
        right: NormalizedClaim,
        left_claim_id: Optional[str] = None,
        right_claim_id: Optional[str] = None,
    ) -> Optional[Conflict]:
        claim_ids = [cid for cid in (left_claim_id, right_claim_id) if cid]

        subject_conflict = self._subject_conflict(left, right)
        if subject_conflict:
            return Conflict(
                conflict_type="subject",
                severity="critical",
                summary=subject_conflict,
                claim_ids=claim_ids,
            )

        period_conflict = self._period_conflict(left, right)
        if period_conflict:
            return Conflict(
                conflict_type="period",
                severity="critical",
                summary=period_conflict,
                claim_ids=claim_ids,
            )

        unit_conflict = self._unit_conflict(left, right)
        if unit_conflict:
            return Conflict(
                conflict_type="unit",
                severity="major",
                summary=unit_conflict,
                claim_ids=claim_ids,
            )

        scope_conflict = self._scope_conflict(left, right)
        if scope_conflict:
            return Conflict(
                conflict_type="scope",
                severity="major",
                summary=scope_conflict,
                claim_ids=claim_ids,
            )

        value_conflict = self._value_conflict(left, right)
        if value_conflict:
            return Conflict(
                conflict_type="value",
                severity=self._value_severity(left),
                summary=value_conflict,
                claim_ids=claim_ids,
            )

        return None

    def _subject_conflict(self, left: NormalizedClaim, right: NormalizedClaim) -> Optional[str]:
        left_subject = left.subject_entity_id or left.subject_text
        right_subject = right.subject_entity_id or right.subject_text
        if left_subject and right_subject and left_subject != right_subject:
            return f"主体不同：{left_subject} 与 {right_subject}"
        return None

    def _period_conflict(self, left: NormalizedClaim, right: NormalizedClaim) -> Optional[str]:
        if left.period and right.period and left.period != right.period:
            return f"期间不同：{left.period} 与 {right.period}"
        return None

    def _unit_conflict(self, left: NormalizedClaim, right: NormalizedClaim) -> Optional[str]:
        if left.unit and right.unit and left.unit != right.unit:
            return f"单位不同：{left.unit} 与 {right.unit}"
        return None

    def _scope_conflict(self, left: NormalizedClaim, right: NormalizedClaim) -> Optional[str]:
        left_scope = left.accounting_scope
        right_scope = right.accounting_scope
        if left_scope and right_scope and left_scope != right_scope:
            return f"会计口径不同：{left_scope} 与 {right_scope}"
        return None

    def _value_conflict(self, left: NormalizedClaim, right: NormalizedClaim) -> Optional[str]:
        left_values = self._extract_values(left.object_value)
        right_values = self._extract_values(right.object_value)
        if not left_values or not right_values:
            return None
        for left_value, right_value in zip(left_values, right_values):
            if self._decimal(left_value) != self._decimal(right_value):
                return f"关键数值不兼容：{left_value} 与 {right_value}"
        return None

    def _value_severity(self, claim: NormalizedClaim) -> str:
        # 涉及金额或业绩区间的冲突默认 critical，其余 major。
        if claim.object_value.get("type") in {"range", "decimal"}:
            return "critical"
        return "major"

    def _extract_values(self, value: dict) -> list[str]:
        if value.get("type") == "range":
            return [str(value.get("min")), str(value.get("max"))]
        return [str(value.get("value"))]

    def _decimal(self, value: Optional[str]) -> Optional[Decimal]:
        if value in (None, "None", ""):
            return None
        try:
            return Decimal(value)
        except Exception:  # noqa: BLE001 - 无法比较的值视为不等
            return Decimal(value or "")
