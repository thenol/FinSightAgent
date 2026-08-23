"""影响分析上下文构建器：生成带时间截面和检索追踪的输入快照。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.domain import Event, FactCard, RetrievalRequest
from app.platform.repository import Repository
from app.retrieval.service import RetrievalService


@dataclass(frozen=True)
class ImpactContext:
    as_of: datetime
    claims: list[Any]
    fact_card: FactCard | None
    entities: list[Any]
    retrieval_items: list[dict[str, Any]]
    data_availability: dict[str, str]
    warnings: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "claims": [
                {
                    "id": claim.id,
                    "predicate": claim.predicate,
                    "object_value": claim.object_value,
                    "status": claim.status,
                    "confidence": claim.confidence,
                    "as_of": claim.as_of.isoformat(),
                    "evidence_ids": claim.evidence_ids,
                }
                for claim in self.claims
            ],
            "fact_card": {
                "id": self.fact_card.id,
                "version": self.fact_card.version,
                "summary": self.fact_card.summary,
                "as_of": self.fact_card.as_of.isoformat(),
            } if self.fact_card else None,
            "entities": [
                {"id": entity.id, "type": entity.entity_type, "name": entity.canonical_name}
                for entity in self.entities
            ],
            "retrieval_items": self.retrieval_items,
            "data_availability": self.data_availability,
            "warnings": self.warnings,
        }


class ImpactContextBuilder:
    """按事件发生时间构建可重放的影响分析上下文。"""

    def __init__(self, repository: Repository, retrieval: RetrievalService | None = None) -> None:
        self.repository = repository
        self.retrieval = retrieval or RetrievalService(repository)

    def build(self, event: Event) -> ImpactContext:
        as_of = event.occurred_at or datetime.now(timezone.utc)
        all_claims = self.repository.get_claims_for_event(event.id, as_of=as_of)
        claims = [claim for claim in all_claims if claim.status == "verified"]
        warnings: list[str] = []
        if len(claims) < len(all_claims):
            warnings.append("unverified_claims_excluded")

        fact_card = self.repository.get_fact_card_for_event(event.id, as_of=as_of)
        if fact_card and fact_card.status not in {"approved", "published"}:
            warnings.append("unapproved_fact_card_excluded")
            fact_card = None

        entities = [
            entity
            for entity_id in event.entity_ids
            if (entity := self.repository.get_entity(entity_id)) is not None
        ]

        retrieval_items: list[dict[str, Any]] = []
        try:
            trace = self.retrieval.retrieve(
                RetrievalRequest(
                    query=event.title,
                    top_k=8,
                    as_of=as_of,
                    retrieval_mode="hybrid",
                )
            )
            retrieval_items = [
                {
                    "chunk_id": item.chunk_id,
                    "document_id": item.document_id,
                    "text": item.text,
                    "score": item.score,
                    "backend": item.backend,
                    "as_of": as_of.isoformat(),
                }
                for item in trace.items
            ]
        except Exception as exc:  # 检索不可用不应阻塞事实分析
            warnings.append(f"retrieval_unavailable:{type(exc).__name__}")

        return ImpactContext(
            as_of=as_of,
            claims=claims,
            fact_card=fact_card,
            entities=entities,
            retrieval_items=retrieval_items,
            data_availability={
                "verified_claims": "available" if claims else "unavailable",
                "retrieval": "available" if retrieval_items else "unavailable",
                "market_data": "unavailable",
            },
            warnings=warnings,
        )
