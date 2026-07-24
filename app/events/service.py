import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain import Document, Event, MergeReviewTask
from app.events.classifier import ClassificationResult, EventClassifier
from app.events.entities import EntityResolver
from app.events.importance import ImportanceCalculator
from app.events.schemas import GENERAL_MARKET_NEWS, is_non_mvp_event_type
from app.events.time_parser import DeterministicEventTimeParser, EventTimeResolution
from app.platform.ids import new_id
from app.platform.repository import Repository


class EventService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.resolver = EntityResolver(repository)
        self.classifier = EventClassifier()
        self.importance_calculator = ImportanceCalculator()
        self.time_parser = DeterministicEventTimeParser()
        self._time_resolutions: dict[str, EventTimeResolution] = {}

    def classify(self, document: Document):
        return self.classifier.classify(document)

    def resolve_event_time(
        self,
        document: Document,
        event_type: str,
        key_fields: dict[str, Any],
        *,
        as_of: Optional[datetime] = None,
    ) -> EventTimeResolution:
        """Resolve business occurrence time while keeping Event's schema unchanged."""
        return self.time_parser.parse(
            event_type=event_type,
            key_fields=key_fields,
            text=document.content,
            published_at=document.published_at,
            ingested_at=document.ingested_at,
            as_of=as_of or document.published_at,
        )

    def get_time_resolution(self, event_id: str) -> Optional[EventTimeResolution]:
        """Return resolution metadata for events handled by this service instance."""
        return self._time_resolutions.get(event_id)

    def create_event(
        self,
        document: Document,
        *,
        classification: Optional[ClassificationResult] = None,
    ) -> Event:
        result = classification or self.classifier.classify(document)
        return self._persist_event(document, result)

    def _persist_event(self, document: Document, result: ClassificationResult) -> Event:
        time_resolution = self.resolve_event_time(
            document,
            result.event_type,
            result.key_fields,
        )
        document_text = f"{document.title}\n{document.content}"
        resolutions = self.resolver.resolve(document_text, document.id)
        market_codes = [resolution.market_code for resolution in resolutions]
        links = self.resolver.to_links(resolutions)
        ambiguous = self.resolver.ambiguous_candidates(resolutions)

        if result.event_type == GENERAL_MARKET_NEWS:
            status = "dormant"
        elif is_non_mvp_event_type(result.event_type):
            status = "archived"
        elif result.needs_review:
            status = "needs_review"
        else:
            status = "triaged"

        importance = self.importance_calculator.calculate(
            event_type=result.event_type,
            source_tier=document.source_tier,
            key_fields=result.key_fields,
            published_at=document.published_at,
        )

        event = Event(
            id=new_id("evt"),
            event_type=result.event_type,
            status=status,
            title=document.title,
            entity_ids=market_codes,
            document_ids=[document.id],
            importance=importance.importance,
            urgency=importance.urgency,
            occurred_at=time_resolution.occurred_at,
            entity_links=links,
            key_fields=result.key_fields,
            confidence=result.confidence,
            classifier_version=result.schema_version,
            missing_required=result.missing_required,
        )
        self._time_resolutions[event.id] = time_resolution
        self.repository.save_event(event)
        self.repository.save_event_entities(event.id, links)
        for candidate in ambiguous:
            self.repository.save_merge_review_task(
                MergeReviewTask(
                    id=new_id("mrt"),
                    document_id=document.id,
                    candidates=[candidate.market_code],
                    status="open",
                    created_at=datetime.now(timezone.utc),
                )
            )
        return event

    def attach_document_to_event(self, event: Event, document: Document) -> Event:
        """将同一事件的新来源或新公告关联到现有 Event。"""
        result = self.classifier.classify(document)
        time_resolution = self.resolve_event_time(
            document,
            event.event_type,
            result.key_fields,
        )
        document_text = f"{document.title}\n{document.content}"
        resolutions = self.resolver.resolve(document_text, document.id)
        links = self.resolver.to_links(resolutions)
        importance = self.importance_calculator.calculate(
            event_type=event.event_type,
            source_tier=document.source_tier,
            key_fields=result.key_fields,
            published_at=document.published_at,
        )
        entity_ids = list(dict.fromkeys([*event.entity_ids, *(link.market_code for link in links)]))
        document_ids = list(dict.fromkeys([*event.document_ids, document.id]))
        merged_key_fields = {**event.key_fields, **result.key_fields}
        merged_links = [*event.entity_links]
        known_entities = {link.entity_id for link in merged_links}
        merged_links.extend(link for link in links if link.entity_id not in known_entities)
        status = "needs_review" if result.needs_review else event.status
        occurred_at = (
            time_resolution.occurred_at
            if time_resolution.resolution_method != "published_at_fallback"
            else event.occurred_at
        )
        updated = replace(
            event,
            status=status,
            entity_ids=entity_ids,
            document_ids=document_ids,
            importance=max(event.importance, importance.importance),
            urgency=self._higher_urgency(event.urgency, importance.urgency),
            occurred_at=occurred_at,
            entity_links=merged_links,
            key_fields=merged_key_fields,
            confidence=max(event.confidence, result.confidence),
            missing_required=sorted(set(event.missing_required) | set(result.missing_required)),
            version=event.version + 1,
        )
        self._time_resolutions[updated.id] = time_resolution
        self.repository.update_event(updated)
        self.repository.save_event_entities(updated.id, links)
        return updated

    def _higher_urgency(self, first: str, second: str) -> str:
        ranks = {"low": 0, "normal": 1, "high": 2, "critical": 3}
        return first if ranks.get(first, 0) >= ranks.get(second, 0) else second


# 保留向后兼容：旧代码可能直接 import SECURITY_CODE 正则。
SECURITY_CODE = re.compile(r"(?<!\d)([036]\d{5})(?:\.(SZ|SH))?(?!\d)", re.IGNORECASE)
