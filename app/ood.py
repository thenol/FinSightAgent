"""OOD detection and observation lifecycle for unknown but relevant events."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.capabilities import default_capability_registry
from app.domain import Document, Event, OODObservation
from app.events.router import RouterDecision
from app.platform.ids import new_id

_FINANCIAL_HINTS = (
    "股市",
    "股票",
    "券商",
    "交易所",
    "银行",
    "支付",
    "清算",
    "基金",
    "市场",
    "指数",
    "流动性",
    "投资者",
    "金融",
)


@dataclass(frozen=True)
class OODDetection:
    is_ood: bool
    ood_score: float
    financial_relevance: float
    closest_known_types: list[dict[str, Any]]
    reasons: list[str]
    recommended_action: str


class OODDetectionService:
    """Deterministic first-stage detector; model-based embeddings can be added later."""

    schema_version = "ood-detector-v1"

    def __init__(self, repository) -> None:
        self.repository = repository
        self.capability_registry = default_capability_registry()

    def detect(self, document: Document, decision: RouterDecision) -> OODDetection:
        text = f"{document.title}\n{document.content}"
        known = self.capability_registry.resolve_for_event(decision.event_type)
        relevance = self._relevance(text, decision)
        unknown_type = decision.is_candidate_type or known is None
        low_confidence = decision.confidence < 0.65
        ood_score = round(
            min(
                1.0,
                (0.55 if unknown_type else 0.10)
                + (0.25 if low_confidence else 0.0)
                + (0.20 if decision.used_fallback else 0.0),
            ),
            4,
        )
        is_ood = relevance >= 0.70 and ood_score >= 0.75
        reasons: list[str] = []
        if unknown_type:
            reasons.append("no_active_capability_pack_for_event_type")
        if low_confidence:
            reasons.append("router_confidence_below_ood_threshold")
        if decision.is_candidate_type:
            reasons.append("router_emitted_candidate_event_type")
        return OODDetection(
            is_ood=is_ood,
            ood_score=ood_score,
            financial_relevance=relevance,
            closest_known_types=self._closest_types(decision.event_type),
            reasons=reasons,
            recommended_action=("create_observation" if is_ood else "generic_or_known_route"),
        )

    def observe(
        self,
        document: Document,
        event: Event,
        decision: RouterDecision,
        detection: OODDetection,
    ) -> OODObservation | None:
        if not detection.is_ood:
            return None
        pack = self.capability_registry.resolve_for_event("general_market_news")
        observation = OODObservation(
            id=new_id("ood"),
            event_id=event.id,
            document_id=document.id,
            status="ready_for_clustering",
            ood_score=detection.ood_score,
            financial_relevance=detection.financial_relevance,
            closest_known_types=detection.closest_known_types,
            extracted_features=self._features(document, decision),
            classifier_version=event.classifier_version,
            router_version="v2",
            generic_pack_id=pack.manifest.pack_id if pack else None,
            generic_pack_version=pack.manifest.version if pack else None,
            observed_at=datetime.now(timezone.utc),
            as_of=document.published_at,
        )
        self.repository.save_ood_observation(observation)
        return observation

    def _relevance(self, text: str, decision: RouterDecision) -> float:
        if decision.relevance == "irrelevant":
            return 0.20
        hints = sum(1 for hint in _FINANCIAL_HINTS if hint in text)
        keyword_score = min(0.65, hints * 0.13)
        route_score = 0.30 if decision.relevance == "relevant" else 0.12
        return round(min(1.0, keyword_score + route_score), 4)

    def _closest_types(self, event_type: str) -> list[dict[str, Any]]:
        return [{"event_type": event_type, "score": 0.25}]

    def _features(self, document: Document, decision: RouterDecision) -> dict[str, Any]:
        text = f"{document.title}\n{document.content}"
        return {
            "title_tokens": re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", document.title)[:32],
            "router_event_type": decision.event_type,
            "router_relevance": decision.relevance,
            "required_agents": list(decision.required_agents),
            "text_length": len(text),
        }
