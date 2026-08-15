"""Event Router v2：相关性门控 + 开放分类（DD-21）。

v1 语义（accept/reject/unsure + 类型白名单）让规则词表外的重大事件被静默归档。
v2 将门控改为经济相关性裁决：类型只是输出标签与路由提示，LLM 可给出白名单外的
候选类型标签（candidate type），落库后进入 needs_review 等待人工确认。

规则分类器只提供 hint；Router 通过 ModelGateway 输出结构化裁决：
relevance（relevant/irrelevant/unsure）决定是否进入事件管道，event_type 只是标签。
"""

from __future__ import annotations

import re
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
    is_candidate_event_type,
    is_mvp_event_type,
    is_non_mvp_event_type,
)
from app.model_gateway.providers import ProviderError
from app.model_gateway.service import ModelGateway, ModelRequest

ROUTER_SCHEMA_VERSION = "v2"
ROUTER_OPERATION = "event_route"

DEFAULT_REQUIRED_AGENTS = (
    "fact_checker",
    "company_analyst",
    "skeptic",
    "synthesizer",
)

# 候选类型标签必须为 snake_case，防止模型输出任意文本污染 event_type
_CANDIDATE_LABEL = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

ROUTER_SYSTEM_PROMPT = (
    "你是财经情报入口路由。判断输入内容的经济/金融市场相关性，并给出事件类型标签。\n"
    "\n"
    "只输出 JSON，字段如下：\n"
    '- relevance: "relevant"（有明确经济意义，值得分析）| "irrelevant"（与经济金融无关）\n'
    '  | "unsure"（无法判断）\n'
    "- event_type: snake_case 标签。已知类型优先取白名单；白名单外的重大经济事件可给出新标签\n"
    "  （如 geopolitical_crisis、weather_event），保留值仅限 general_market_news 与 out_of_scope\n"
    "- importance: 0~1，事件对市场的预期重要度\n"
    "- confidence: 0~1，你对本次裁决的置信度\n"
    "- required_agents: 建议调用的分析 Agent 列表（可为空）\n"
    "- reason: 不超过 200 字的裁决理由\n"
    "\n"
    '门控原则：相关性是唯一门槛，不要因为"不认识事件类型"而判 irrelevant。'
)


class RouterOutput(BaseModel):
    """受限 Schema（v2）：Router 只能在此结构内裁决。"""

    model_config = ConfigDict(extra="forbid")

    relevance: str = Field(pattern="^(relevant|irrelevant|unsure)$")
    event_type: str = Field(min_length=1, max_length=40)
    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    required_agents: list[str] = Field(default_factory=list, max_length=16)
    reason: str = Field(default="", max_length=2000)

    @field_validator("event_type")
    @classmethod
    def _normalize_type(cls, value: str) -> str:
        return value.strip()


@dataclass(frozen=True)
class RouterDecision:
    relevance: str  # relevant | irrelevant | unsure
    event_type: str
    importance: float | None = None  # 候选类型的 Router 建议重要度
    confidence: float = 0.0
    required_agents: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
    model_run_id: str | None = None
    rule_hint_type: str = ""
    used_fallback: bool = False
    is_candidate_type: bool = False


def deterministic_route_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """DeterministicProvider / 离线回放：跟随规则 hint，不放行未知类型。"""
    hint = str(payload.get("rule_hint_type") or OUT_OF_SCOPE)
    hint_confidence = float(payload.get("rule_hint_confidence") or 0.40)
    if hint in MVP_EVENT_TYPES:
        schema = get_schema(hint)
        return {
            "relevance": "relevant",
            "event_type": hint,
            "importance": schema.importance if schema else 0.5,
            "confidence": max(hint_confidence, 0.85),
            "required_agents": list(DEFAULT_REQUIRED_AGENTS),
            "reason": "deterministic_relevant_rule_hint",
        }
    if hint == GENERAL_MARKET_NEWS:
        return {
            "relevance": "unsure",
            "event_type": GENERAL_MARKET_NEWS,
            "importance": 0.35,
            "confidence": max(hint_confidence, 0.55),
            "required_agents": [],
            "reason": "deterministic_unsure_general_news",
        }
    return {
        "relevance": "irrelevant",
        "event_type": OUT_OF_SCOPE,
        "importance": 0.10,
        "confidence": hint_confidence,
        "required_agents": [],
        "reason": "deterministic_irrelevant_out_of_scope",
    }


class EventRouter:
    """入口 Router：规则提名 + 模型相关性裁决 + 开放类型标签。"""

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
                    system_prompt=ROUTER_SYSTEM_PROMPT,
                )
            )
            raw = response.payload
            # DeterministicProvider 默认回显 input；若无结构化 relevance 则本地合成
            if "relevance" not in raw:
                raw = deterministic_route_payload(payload)
            parsed = RouterOutput.model_validate(raw)
            decision = RouterDecision(
                relevance=parsed.relevance,
                event_type=parsed.event_type,
                importance=parsed.importance,
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
                relevance=parsed.relevance,
                event_type=parsed.event_type,
                importance=parsed.importance,
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
        if decision.relevance == "relevant":
            if is_mvp_event_type(decision.event_type):
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
                    schema_version="event-router-v2",
                )
            # 候选类型：LLM 开放分类产出的一等词表外标签
            return ClassificationResult(
                event_type=decision.event_type,
                importance=decision.importance if decision.importance is not None else 0.35,
                confidence=decision.confidence,
                key_fields=hint.key_fields,
                missing_required=["candidate_type_confirmation"],
                schema_version="event-router-v2-candidate",
            )

        if decision.relevance == "unsure":
            # 不确定：若规则已提名一等类型则保留类型但强制 needs_review；否则综合资讯
            if is_mvp_event_type(hint.event_type):
                return replace(
                    hint,
                    confidence=min(hint.confidence, decision.confidence, 0.49),
                    # 人为制造 missing 以触发 needs_review（若已有 missing 则保持）
                    missing_required=hint.missing_required or ["router_confirmation"],
                    schema_version="event-router-v2",
                )
            return ClassificationResult(
                event_type=GENERAL_MARKET_NEWS,
                importance=0.35,
                confidence=decision.confidence,
                schema_version="event-router-v2",
            )

        # irrelevant：无经济相关性，归档
        return ClassificationResult(
            event_type=OUT_OF_SCOPE,
            importance=0.15,
            confidence=decision.confidence,
            schema_version="event-router-v2",
        )

    def _sanitize(
        self,
        decision: RouterDecision,
        hint: ClassificationResult,
        text: str,
    ) -> RouterDecision:
        relevance = decision.relevance
        event_type = decision.event_type
        agents = decision.required_agents
        is_candidate = False
        importance = decision.importance

        if relevance == "relevant":
            if is_mvp_event_type(event_type):
                if not agents:
                    agents = DEFAULT_REQUIRED_AGENTS
            elif (
                _CANDIDATE_LABEL.match(event_type)
                and is_candidate_event_type(event_type)
            ):
                # 合法的候选类型标签
                is_candidate = True
            else:
                # 模型说 relevant 但类型非法（保留字冲突/非 snake_case）→ 降级不确定
                relevance = "unsure"
                event_type = (
                    hint.event_type if is_mvp_event_type(hint.event_type) else GENERAL_MARKET_NEWS
                )
                agents = ()
                importance = None
        elif relevance == "irrelevant":
            event_type = OUT_OF_SCOPE
            agents = ()
            importance = None
        else:  # unsure
            if not event_type or not (
                is_mvp_event_type(event_type) or is_non_mvp_event_type(event_type)
            ):
                event_type = (
                    hint.event_type if hint.event_type else fallback_event_type(text)
                )
                if not (
                    is_mvp_event_type(event_type) or is_non_mvp_event_type(event_type)
                ):
                    event_type = GENERAL_MARKET_NEWS
            agents = ()

        return RouterDecision(
            relevance=relevance,
            event_type=event_type,
            importance=importance,
            confidence=decision.confidence,
            required_agents=tuple(agents),
            reason=decision.reason,
            model_run_id=decision.model_run_id,
            rule_hint_type=decision.rule_hint_type or hint.event_type,
            used_fallback=decision.used_fallback,
            is_candidate_type=is_candidate,
        )
