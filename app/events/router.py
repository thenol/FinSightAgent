"""Event Router：入口调度 Agent（不做深度研究）。

规则分类器只提供 hint；Router 通过 ModelGateway 输出结构化裁决：
accept / reject / unsure，决定是否升格为可研究的五类 Event。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain import Document
from app.events.classifier import ClassificationResult, EventClassifier
from app.events.schemas import (
    GENERAL_MARKET_NEWS,
    MVP_EVENT_TYPES,
    OUT_OF_SCOPE,
    fallback_event_type,
    get_schema,
    is_mvp_event_type,
)
from app.model_gateway.providers import ProviderError
from app.model_gateway.service import ModelGateway, ModelRequest

ROUTER_SCHEMA_VERSION = "v1"
ROUTER_OPERATION = "event_route"

DEFAULT_REQUIRED_AGENTS = (
    "fact_checker",
    "company_analyst",
    "skeptic",
    "synthesizer",
)


class RouterOutput(BaseModel):
    """受限 Schema：Router 只能在此结构内裁决。"""

    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(accept|reject|unsure)$")
    event_type: str = Field(min_length=1, max_length=40)
    confidence: float = Field(ge=0.0, le=1.0)
    required_agents: list[str] = Field(default_factory=list, max_length=16)
    reason: str = Field(default="", max_length=2000)

    @field_validator("event_type")
    @classmethod
    def _normalize_type(cls, value: str) -> str:
        return value.strip()


@dataclass(frozen=True)
class RouterDecision:
    decision: str
    event_type: str
    confidence: float
    required_agents: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
    model_run_id: str | None = None
    rule_hint_type: str = ""
    used_fallback: bool = False


def deterministic_route_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """DeterministicProvider / 离线回放：跟随规则 hint，不发明新类型。"""
    hint = str(payload.get("rule_hint_type") or OUT_OF_SCOPE)
    if hint in MVP_EVENT_TYPES:
        return {
            "decision": "accept",
            "event_type": hint,
            "confidence": float(payload.get("rule_hint_confidence") or 0.85),
            "required_agents": list(DEFAULT_REQUIRED_AGENTS),
            "reason": "deterministic_accept_rule_hint",
        }
    if hint == GENERAL_MARKET_NEWS:
        return {
            "decision": "reject",
            "event_type": GENERAL_MARKET_NEWS,
            "confidence": float(payload.get("rule_hint_confidence") or 0.55),
            "required_agents": [],
            "reason": "deterministic_reject_general_news",
        }
    return {
        "decision": "reject",
        "event_type": OUT_OF_SCOPE,
        "confidence": float(payload.get("rule_hint_confidence") or 0.40),
        "required_agents": [],
        "reason": "deterministic_reject_out_of_scope",
    }


class EventRouter:
    """入口 Router：规则提名 + 模型确认。"""

    def __init__(
        self,
        gateway: ModelGateway,
        classifier: EventClassifier | None = None,
    ) -> None:
        self.gateway = gateway
        self.classifier = classifier or EventClassifier()

    def propose(self, document: Document) -> ClassificationResult:
        """规则层 hint（不当最终盖章）。"""
        return self.classifier.classify(document)

    def route(
        self,
        document: Document,
        *,
        rule_hint: ClassificationResult | None = None,
    ) -> RouterDecision:
        hint = rule_hint or self.propose(document)
        text = f"{document.title}\n{document.content}"
        excerpt = text[:4000]
        payload = {
            "title": document.title[:500],
            "excerpt": excerpt,
            "source_tier": document.source_tier,
            "rule_hint_type": hint.event_type,
            "rule_hint_confidence": hint.confidence,
            "rule_key_fields": hint.key_fields,
            "mvp_event_types": sorted(MVP_EVENT_TYPES),
        }
        try:
            response = self.gateway.invoke(
                ModelRequest(
                    operation=ROUTER_OPERATION,
                    input_schema_version=ROUTER_SCHEMA_VERSION,
                    output_schema_version=ROUTER_SCHEMA_VERSION,
                    payload=payload,
                    max_cost_usd=1.0,
                )
            )
            raw = response.payload
            # DeterministicProvider 默认回显 input；若无结构化 decision 则本地合成
            if "decision" not in raw:
                raw = deterministic_route_payload(payload)
            parsed = RouterOutput.model_validate(raw)
            decision = RouterDecision(
                decision=parsed.decision,
                event_type=parsed.event_type,
                confidence=parsed.confidence,
                required_agents=tuple(parsed.required_agents or ()),
                reason=parsed.reason,
                model_run_id=response.run_id,
                rule_hint_type=hint.event_type,
                used_fallback=False,
            )
        except (ValidationError, ValueError, KeyError, TypeError, ProviderError):
            raw = deterministic_route_payload(payload)
            parsed = RouterOutput.model_validate(raw)
            decision = RouterDecision(
                decision=parsed.decision,
                event_type=parsed.event_type,
                confidence=parsed.confidence,
                required_agents=tuple(parsed.required_agents or ()),
                reason=parsed.reason,
                model_run_id=None,
                rule_hint_type=hint.event_type,
                used_fallback=True,
            )
        return self._sanitize(decision, hint, text)

    def merge_classification(
        self,
        hint: ClassificationResult,
        decision: RouterDecision,
    ) -> ClassificationResult:
        """将 Router 裁决合并为最终 ClassificationResult（供 EventService 落库）。"""
        if decision.decision == "accept" and is_mvp_event_type(decision.event_type):
            if hint.event_type == decision.event_type:
                return replace(
                    hint,
                    confidence=max(hint.confidence, decision.confidence),
                    schema_version=hint.schema_version or "event-schema-v1",
                )
            schema = get_schema(decision.event_type)
            missing = list(schema.required_fields) if schema else ["router_retyped"]
            return ClassificationResult(
                event_type=decision.event_type,
                importance=schema.importance if schema else hint.importance,
                confidence=decision.confidence,
                key_fields={},
                missing_required=missing,
                schema_version="event-router-v1",
            )

        if decision.decision == "unsure":
            # 不确定：若规则已提名五类则保留类型但强制 needs_review；否则综合资讯
            if is_mvp_event_type(hint.event_type):
                return replace(
                    hint,
                    confidence=min(hint.confidence, decision.confidence, 0.49),
                    # 人为制造 missing 以触发 needs_review（若已有 missing 则保持）
                    missing_required=hint.missing_required or ["router_confirmation"],
                    schema_version="event-router-v1",
                )
            return ClassificationResult(
                event_type=GENERAL_MARKET_NEWS,
                importance=0.35,
                confidence=decision.confidence,
                schema_version="event-router-v1",
            )

        # reject
        event_type = decision.event_type
        if is_mvp_event_type(event_type):
            event_type = GENERAL_MARKET_NEWS
        if event_type not in {GENERAL_MARKET_NEWS, OUT_OF_SCOPE}:
            event_type = GENERAL_MARKET_NEWS
        return ClassificationResult(
            event_type=event_type,
            importance=0.35 if event_type == GENERAL_MARKET_NEWS else 0.15,
            confidence=decision.confidence,
            schema_version="event-router-v1",
        )

    def _sanitize(
        self,
        decision: RouterDecision,
        hint: ClassificationResult,
        text: str,
    ) -> RouterDecision:
        event_type = decision.event_type
        decision_name = decision.decision
        agents = decision.required_agents

        if decision_name == "accept":
            if not is_mvp_event_type(event_type):
                # 模型说 accept 但类型非法 → 降级不确定
                decision_name = "unsure"
                if is_mvp_event_type(hint.event_type):
                    event_type = hint.event_type
                else:
                    event_type = GENERAL_MARKET_NEWS
                agents = ()
            elif not agents:
                agents = DEFAULT_REQUIRED_AGENTS
        elif decision_name == "reject":
            if is_mvp_event_type(event_type):
                event_type = fallback_event_type(text)
            agents = ()
        else:  # unsure
            if not event_type:
                event_type = hint.event_type or GENERAL_MARKET_NEWS
            agents = ()

        return RouterDecision(
            decision=decision_name,
            event_type=event_type,
            confidence=decision.confidence,
            required_agents=tuple(agents),
            reason=decision.reason,
            model_run_id=decision.model_run_id,
            rule_hint_type=decision.rule_hint_type or hint.event_type,
            used_fallback=decision.used_fallback,
        )
