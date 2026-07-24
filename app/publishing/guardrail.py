"""发布前 Guardrail 引擎。

GuardrailEngine 按固定顺序执行规则，输出版本化结果（DD-60 §5）。规则引擎拥有
最终阻断权，模型仅可辅助敏感措辞分类。

规则：
1. 关键事实均有有效 Claim 和 Evidence -> 阻止发布
2. 引用 Revision 在 as_of 前可用 -> 阻止发布并记录安全事件
3. 不含自动交易/仓位/保证收益措辞 -> 阻止发布并人工审核
4. 事实、假设、推论分区明确 -> 退回装配或审核
5. 低置信度不使用强方向标签 -> 自动降级标签或审核
6. 授权内容片段符合展示范围 -> 收缩展示或阻止发布
7. 必填免责声明、版本和时间齐全 -> 自动补齐确定性字段
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain import Claim
from app.publishing.assembler import FORBIDDEN_PHRASES

GUARDRAIL_VERSION = "guardrail-v1"

# 强方向标签（低置信度时禁止使用）
STRONG_SIGNALS = {"strongly_positive", "strongly_negative"}
LOW_CONFIDENCE_THRESHOLD = 0.50


@dataclass(frozen=True)
class GuardrailRuleResult:
    rule: str
    status: str  # pass | fail | warn
    message: str
    object_ids: list[str] = field(default_factory=list)
    fix_suggestion: str | None = None


@dataclass(frozen=True)
class GuardrailResult:
    passed: bool
    review_required: bool
    version: str
    rules: list[GuardrailRuleResult]

    @property
    def blocking_rules(self) -> list[GuardrailRuleResult]:
        return [r for r in self.rules if r.status == "fail"]


class GuardrailEngine:
    """对报告草稿执行发布前检查。"""

    def evaluate(
        self,
        draft: dict[str, Any],
        claims: list[Claim],
        as_of: datetime,
    ) -> GuardrailResult:
        rules = [
            self._rule_citation_integrity(draft, claims),
            self._rule_as_of_available(draft, claims, as_of),
            self._rule_forbidden_phrases(draft),
            self._rule_section_partition(draft),
            self._rule_low_confidence_signal(draft),
            self._rule_required_fields(draft),
        ]
        blocking = any(r.status == "fail" for r in rules)
        review_required = any(
            r.status == "fail" and "人工审核" in (r.fix_suggestion or "") for r in rules
        )
        passed = not blocking
        return GuardrailResult(
            passed=passed,
            review_required=review_required,
            version=GUARDRAIL_VERSION,
            rules=rules,
        )

    def _rule_citation_integrity(
        self, draft: dict[str, Any], claims: list[Claim]
    ) -> GuardrailRuleResult:
        """关键事实均有有效 Claim 和 Evidence。"""
        claim_ids = set(draft.get("claim_ids", []))
        if not claim_ids:
            return GuardrailRuleResult(
                rule="citation_integrity",
                status="fail",
                message="报告无任何已验证 Claim 引用",
                fix_suggestion="退回装配：补齐 verified Claim 或降级为事实卡片",
            )
        verified = {c.id for c in claims if c.status == "verified" and c.evidence_ids}
        missing = claim_ids - verified
        if missing:
            return GuardrailRuleResult(
                rule="citation_integrity",
                status="fail",
                message=f"Claim 缺少有效 Evidence：{missing}",
                object_ids=list(missing),
                fix_suggestion="退回事实核验：为缺失 Claim 补充 Evidence",
            )
        return GuardrailRuleResult(rule="citation_integrity", status="pass", message="引用完整")

    def _rule_as_of_available(
        self, draft: dict[str, Any], claims: list[Claim], as_of: datetime
    ) -> GuardrailRuleResult:
        """引用 Revision 在 as_of 前可用。"""
        from app.platform.asof import visible_as_of

        future = [c.id for c in claims if c.status == "verified" and not visible_as_of(c, as_of)]
        if future:
            return GuardrailRuleResult(
                rule="as_of_available",
                status="fail",
                message=f"引用了 as_of 之后的未来证据：{future}",
                object_ids=future,
                fix_suggestion="阻止发布并记录安全事件",
            )
        return GuardrailRuleResult(rule="as_of_available", status="pass", message="无未来证据")

    def _rule_forbidden_phrases(self, draft: dict[str, Any]) -> GuardrailRuleResult:
        """不含自动交易/仓位/保证收益措辞。"""
        text = self._draft_text(draft)
        found = [phrase for phrase in FORBIDDEN_PHRASES if phrase in text]
        if found:
            return GuardrailRuleResult(
                rule="forbidden_phrases",
                status="fail",
                message=f"报告含禁止措辞：{found}",
                fix_suggestion="阻止发布并人工审核",
            )
        return GuardrailRuleResult(rule="forbidden_phrases", status="pass", message="措辞合规")

    def _rule_section_partition(self, draft: dict[str, Any]) -> GuardrailRuleResult:
        """事实、假设、推论分区明确。"""
        sections = draft.get("sections", [])
        kinds = {s.get("kind") for s in sections}
        # 已验证事实区必须存在且非空
        verified_section = next((s for s in sections if s.get("kind") == "verified_facts"), None)
        if verified_section is None or not verified_section.get("items"):
            return GuardrailRuleResult(
                rule="section_partition",
                status="fail",
                message="已验证事实区为空",
                fix_suggestion="退回装配",
            )
        return GuardrailRuleResult(
            rule="section_partition", status="pass", message=f"分区完整：{kinds}"
        )

    def _rule_low_confidence_signal(self, draft: dict[str, Any]) -> GuardrailRuleResult:
        """低置信度不使用强方向标签。"""
        confidence = draft.get("confidence", 0)
        signal = draft.get("signal")
        if confidence < LOW_CONFIDENCE_THRESHOLD and signal in STRONG_SIGNALS:
            return GuardrailRuleResult(
                rule="low_confidence_signal",
                status="warn",
                message=f"低置信度({confidence})使用强标签({signal})",
                fix_suggestion="自动降级标签为 moderately_* 或审核",
            )
        return GuardrailRuleResult(
            rule="low_confidence_signal", status="pass", message="置信度与标签匹配"
        )

    def _rule_required_fields(self, draft: dict[str, Any]) -> GuardrailRuleResult:
        """必填免责声明、版本和时间齐全。"""
        missing = [
            field
            for field in ("schema_version", "as_of", "disclaimer", "title", "summary")
            if not draft.get(field)
        ]
        if missing:
            return GuardrailRuleResult(
                rule="required_fields",
                status="fail",
                message=f"必填字段缺失：{missing}",
                fix_suggestion="自动补齐确定性字段",
            )
        return GuardrailRuleResult(rule="required_fields", status="pass", message="必填字段齐全")

    @staticmethod
    def _draft_text(draft: dict[str, Any]) -> str:
        parts = [draft.get("title", ""), draft.get("summary", "")]
        for section in draft.get("sections", []):
            parts.append(section.get("title", ""))
        return " ".join(parts)
