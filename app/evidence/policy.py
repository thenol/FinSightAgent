"""来源政策与事实验证。

EvidencePolicyService 按来源等级、独立来源数量、定位质量与冲突决定 Claim 状态
与置信度分项（DD-40 §6、§7）。置信度由规则计算并保存分项，禁止 Agent 单独
决定最终分数。

决策规则（§7.1）：
- 至少一个 S 级直接证据，且无关键反证 -> verified
- 至少一个 A 级直接证据 + 一个独立 A/B 级支持证据 -> verified
- 只有 B/C 级来源 -> unverified
- 同主体、谓词、期间但关键值不兼容 -> conflicted
- 原文不支持、主体错误或引用失效 -> rejected
"""

from dataclasses import dataclass, field
from typing import Optional

from app.evidence.claims import NormalizedClaim

POLICY_VERSION = "policy-v1"

# 来源等级 -> 直接证据是否满足 verified 门槛
TIER_RANK = {"S": 4, "A": 3, "B": 2, "C": 1}


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    source_tier: str
    stance: str  # support | refute | context
    source_independence_key: str


@dataclass(frozen=True)
class PolicyDecision:
    status: str  # verified | unverified | conflicted | rejected
    confidence: float
    reason_code: str
    policy_version: str = POLICY_VERSION
    score_components: dict[str, float] = field(default_factory=dict)
    independent_source_count: int = 0


class EvidencePolicyService:
    """按来源等级、独立性与冲突决定事实状态。"""

    def decide(
        self,
        claim: NormalizedClaim,
        evidence: list[EvidenceRecord],
        has_critical_conflict: bool = False,
    ) -> PolicyDecision:
        if has_critical_conflict:
            return PolicyDecision(
                status="conflicted",
                confidence=0.30,
                reason_code="CRITICAL_VALUE_CONFLICT",
                independent_source_count=self._independent_count(evidence),
                score_components={"conflict_penalty": -0.40},
            )

        supporting = [item for item in evidence if item.stance == "support"]
        refuting = [item for item in evidence if item.stance == "refute"]

        if not supporting:
            return PolicyDecision(
                status="unverified",
                confidence=0.20,
                reason_code="NO_DIRECT_EVIDENCE",
                independent_source_count=0,
                score_components={},
            )

        if refuting:
            return PolicyDecision(
                status="conflicted",
                confidence=0.35,
                reason_code="COUNTER_EVIDENCE_PRESENT",
                independent_source_count=self._independent_count(evidence),
                score_components={"refute_count": float(len(refuting))},
            )

        independent = self._independent_count(supporting)
        s_direct = self._has_tier(supporting, "S")
        a_direct = self._has_tier(supporting, "A")
        independent_support = self._independent_support(supporting)

        if s_direct:
            confidence = self._confidence(supporting, independent)
            return PolicyDecision(
                status="verified",
                confidence=confidence,
                reason_code="S_TIER_DIRECT_EVIDENCE",
                independent_source_count=independent,
                score_components=self._components(supporting, independent, "s_direct"),
            )

        if a_direct and independent_support >= 2:
            confidence = self._confidence(supporting, independent)
            return PolicyDecision(
                status="verified",
                confidence=confidence,
                reason_code="A_TIER_WITH_INDEPENDENT_SUPPORT",
                independent_source_count=independent,
                score_components=self._components(supporting, independent, "a_independent"),
            )

        # 仅 A 级单一来源或 B/C 级来源 -> unverified
        return PolicyDecision(
            status="unverified",
            confidence=0.55,
            reason_code="LOW_TIER_ONLY" if not a_direct else "SINGLE_SOURCE_INSUFFICIENT",
            independent_source_count=independent,
            score_components=self._components(supporting, independent, "insufficient"),
        )

    def _confidence(self, supporting: list[EvidenceRecord], independent: int) -> float:
        base = 0.62
        tier_bonus = 0.20 if self._has_tier(supporting, "S") else 0.10
        independence_bonus = min(independent - 1, 2) * 0.06
        return max(0.0, min(1.0, base + tier_bonus + independence_bonus))

    def _components(
        self,
        supporting: list[EvidenceRecord],
        independent: int,
        basis: str,
    ) -> dict[str, float]:
        return {
            "base": 0.62,
            "tier_bonus": 0.20 if self._has_tier(supporting, "S") else 0.10,
            "independence_bonus": float(min(independent - 1, 2) * 0.06),
            "basis_flag": 1.0 if basis == "s_direct" else 0.0,
        }

    def _has_tier(self, evidence: list[EvidenceRecord], tier: str) -> bool:
        return any(item.source_tier == tier for item in evidence)

    def _independent_count(self, evidence: list[EvidenceRecord]) -> int:
        keys = {item.source_independence_key for item in evidence}
        return len(keys)

    def _independent_support(self, evidence: list[EvidenceRecord]) -> int:
        return self._independent_count(evidence)


def source_independence_key(
    *,
    filing_id: Optional[str] = None,
    disclosure_id: Optional[str] = None,
    media_org_id: Optional[str] = None,
    propagation_root: Optional[str] = None,
) -> str:
    """按 DD-40 §6 优先级生成来源独立性 key。转载共享同一 key 只计一个来源。"""
    for value in (filing_id, disclosure_id, media_org_id, propagation_root):
        if value:
            return value
    return "unknown"
