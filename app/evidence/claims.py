"""Claim 规范化、指纹与匹配。

ClaimNormalizer 把 Agent 提出的原始事实声明规范化为 ``NormalizedClaim``：
统一主体、谓词（受控词表）、类型化值（单位/币种/期间/口径），数字以字符串十进制
传输（DD-00 §3）。ClaimFingerprint 由规范化主体、谓词、值、单位、期间和关键
限定条件生成；原文措辞、置信度和 Evidence ID 不参与指纹（DD-40 §5）。

ClaimMatcher 依据指纹识别重复、补充或替代事实（DD-40 §9）。
"""

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from app.evidence.predicates import PREDICATE_VERSION, get_predicate


class ClaimNormalizationError(ValueError):
    """规范化失败：谓词不受控、值类型不符或必填字段缺失。"""


@dataclass(frozen=True)
class NormalizedClaim:
    """规范化后的事实声明，用于生成指纹与匹配。"""

    subject_text: str
    subject_entity_id: Optional[str]
    predicate: str
    object_value: dict[str, Any]
    qualifiers: dict[str, Any]
    as_of: str
    predicate_version: str = PREDICATE_VERSION

    @property
    def period(self) -> Optional[str]:
        return self.qualifiers.get("period")

    @property
    def unit(self) -> Optional[str]:
        return self.object_value.get("unit")

    @property
    def accounting_scope(self) -> Optional[str]:
        return self.qualifiers.get("accounting_scope")


class ClaimNormalizer:
    """校验谓词与值类型，并产出规范化值对象。"""

    def normalize(
        self,
        *,
        subject_text: str,
        subject_entity_id: Optional[str],
        predicate: str,
        object_value: dict[str, Any],
        qualifiers: Optional[dict[str, Any]] = None,
        as_of: str,
    ) -> NormalizedClaim:
        spec = get_predicate(predicate)
        if spec is None:
            raise ClaimNormalizationError(
                f"unsupported predicate: {predicate}", "PREDICATE_UNSUPPORTED"
            )

        if not subject_text and not subject_entity_id:
            raise ClaimNormalizationError("subject required", "SUBJECT_REQUIRED")

        normalized_value = self._normalize_object(object_value, expected_type=spec.object_type)
        normalized_qualifiers = {
            key: value for key, value in (qualifiers or {}).items() if value is not None
        }
        return NormalizedClaim(
            subject_text=subject_text or "",
            subject_entity_id=subject_entity_id,
            predicate=predicate,
            object_value=normalized_value,
            qualifiers=normalized_qualifiers,
            as_of=as_of,
        )

    def _normalize_object(self, value: dict[str, Any], expected_type: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ClaimNormalizationError("object must be an object", "OBJECT_INVALID")
        declared_type = value.get("type")
        if declared_type != expected_type:
            raise ClaimNormalizationError(
                f"object type mismatch: expected {expected_type}, got {declared_type}",
                "OBJECT_TYPE_MISMATCH",
            )
        if expected_type == "decimal":
            return self._normalize_decimal(value)
        if expected_type == "range":
            return self._normalize_range(value)
        return dict(value)

    def _normalize_decimal(self, value: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(value)
        raw = normalized.get("value")
        if raw is None:
            raise ClaimNormalizationError("decimal value required", "VALUE_REQUIRED")
        try:
            normalized["value"] = format(Decimal(str(raw)), "f")
        except (InvalidOperation, ValueError) as exc:
            raise ClaimNormalizationError("decimal value invalid", "VALUE_INVALID") from exc
        return normalized

    def _normalize_range(self, value: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(value)
        for key in ("min", "max"):
            if key in normalized and normalized[key] is not None:
                try:
                    normalized[key] = format(Decimal(str(normalized[key])), "f")
                except (InvalidOperation, ValueError) as exc:
                    raise ClaimNormalizationError(f"range {key} invalid", "VALUE_INVALID") from exc
        return normalized


class ClaimFingerprint:
    """生成事实指纹：规范化主体+谓词+值+单位+期间+口径。"""

    def compute(self, claim: NormalizedClaim) -> str:
        canonical = json.dumps(
            {
                "subject_entity_id": claim.subject_entity_id or claim.subject_text,
                "predicate": claim.predicate,
                "object": self._canonical_object(claim.object_value),
                "qualifiers": self._canonical_qualifiers(claim.qualifiers),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _canonical_object(self, value: dict[str, Any]) -> dict[str, Any]:
        keys = ("type", "value", "min", "max", "unit", "currency")
        return {key: value[key] for key in keys if key in value}

    def _canonical_qualifiers(self, qualifiers: dict[str, Any]) -> dict[str, Any]:
        # 业务模板字段同样是事实身份的一部分：例如同金额但不同对手方、
        # 股东或监管机构不能因指纹相同而被错误复用。
        keys = (
            "period",
            "accounting_scope",
            "comparison",
            "event_type",
            "key_field",
            "profit_metric",
            "counterparties",
            "duration",
            "transaction_type",
            "stage",
            "valuation",
            "shareholder",
            "ownership_ratio",
            "shares",
            "authority",
            "subject",
            "reason",
        )
        return {key: qualifiers[key] for key in keys if key in qualifiers}


@dataclass(frozen=True)
class ClaimMatch:
    relation: str  # duplicate | supersedes | qualifies | new
    existing_claim_id: Optional[str] = None
    fingerprint: str = ""


class ClaimMatcher:
    """依据指纹与替代关系识别重复、补充或替代事实。"""

    def match(
        self,
        claim: NormalizedClaim,
        fingerprint: str,
        existing: list[tuple[str, str]],
    ) -> ClaimMatch:
        for existing_id, existing_fingerprint in existing:
            if existing_fingerprint == fingerprint:
                return ClaimMatch(
                    relation="duplicate", existing_claim_id=existing_id, fingerprint=fingerprint
                )
        return ClaimMatch(relation="new", fingerprint=fingerprint)


@dataclass(frozen=True)
class ClaimEvidenceLink:
    evidence_id: str
    stance: str  # support | refute | context
    source_independence_key: str
    weight: float = field(default=1.0)
