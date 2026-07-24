"""事件匹配与聚类。

EventMatcher 判断一篇新文档应合并到已有事件、新建事件，还是进入人工审核
（DD-20 §6、IMP-022）。

候选召回：至少一个主要实体相同、事件类型相同或兼容、发布时间位于类型配置窗口内
（默认 30 天，并购重组 180 天）。

特征评分：
    match_score =
        0.35 * entity_overlap
      + 0.25 * type_compatibility
      + 0.20 * key_field_similarity
      + 0.10 * time_proximity
      + 0.10 * title_similarity

否决条件：不同合同对手方、不同业绩期间等关键字段冲突直接否决合并。

决策：
- score >= 0.85 且无否决：自动合并。
- score < 0.55：新建事件。
- 中间区间或存在冲突：创建 MergeReviewTask。

所有决策保存候选集、特征、分数、规则版本和最终决定，便于评估误合并与漏合并。
"""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

from app.domain import Document, Event, MatchDecision, MatchFeatures
from app.events.schemas import EVENT_SCHEMAS
from app.platform.ids import new_id
from app.platform.repository import Repository

RULE_VERSION = "event-matcher-v1"
MERGE_THRESHOLD = 0.85
NEW_THRESHOLD = 0.55

DEFAULT_WINDOW_DAYS = 30
MERGER_WINDOW_DAYS = 180


class EventMatcher:
    """对新文档召回候选事件并决定合并、新建或审核。"""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def find_match(
        self, document: Document, candidate_event_type: str, candidate_key_fields: dict[str, Any]
    ) -> tuple[Optional[Event], MatchFeatures, Optional[Event]]:
        """返回 (匹配到的事件或None, 特征, 最佳候选)。"""
        candidates = self._recall(document, candidate_event_type)
        if not candidates:
            return None, self._empty_features(), None

        best_event: Optional[Event] = None
        best_features: Optional[MatchFeatures] = None
        for candidate in candidates:
            features = self._score(document, candidate, candidate_key_fields)
            if best_features is None or features.score > best_features.score:
                best_event = candidate
                best_features = features

        assert best_features is not None
        if best_features.vetoed or not (best_features.score >= MERGE_THRESHOLD):
            return None, best_features, best_event
        return best_event, best_features, best_event

    def record_decision(
        self,
        document: Document,
        candidate_event: Optional[Event],
        features: MatchFeatures,
        decision: str,
    ) -> MatchDecision:
        record = MatchDecision(
            id=new_id("mcd"),
            document_id=document.id,
            candidate_event_id=candidate_event.id if candidate_event else None,
            features=asdict(features),
            score=features.score,
            rule_version=RULE_VERSION,
            decision=decision,
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_match_decision(record)
        return record

    def decide(self, features: MatchFeatures) -> str:
        if features.vetoed:
            return "review"
        if features.score >= MERGE_THRESHOLD:
            return "merged"
        if features.score < NEW_THRESHOLD:
            return "new"
        return "review"

    def _recall(self, document: Document, event_type: str) -> list[Event]:
        window_days = (
            MERGER_WINDOW_DAYS if event_type == "merger_acquisition" else DEFAULT_WINDOW_DAYS
        )
        cutoff = document.published_at - timedelta(days=window_days)
        events = self.repository.list_events(as_of=document.published_at)
        candidates: list[Event] = []
        for event in events:
            if event.occurred_at < cutoff:
                continue
            if event.event_type != event_type:
                continue
            if self._shares_entity(document, event):
                candidates.append(event)
        return candidates

    def _shares_entity(self, document: Document, event: Event) -> bool:
        document_text = f"{document.title}\n{document.content}"
        for market_code in event.entity_ids:
            if market_code in document_text:
                return True
        return False

    def _score(
        self,
        document: Document,
        candidate: Event,
        candidate_key_fields: dict[str, Any],
    ) -> MatchFeatures:
        entity_overlap = self._entity_overlap(document, candidate)
        type_compatibility = 1.0 if candidate.event_type == self._infer_type(document) else 0.0
        key_field_similarity, vetoed, veto_reason = self._key_field_similarity_and_veto(
            candidate, candidate_key_fields
        )
        time_proximity = self._time_proximity(document, candidate)
        title_similarity = self._title_similarity(document, candidate)

        return MatchFeatures(
            entity_overlap=entity_overlap,
            type_compatibility=type_compatibility,
            key_field_similarity=key_field_similarity,
            time_proximity=time_proximity,
            title_similarity=title_similarity,
            vetoed=vetoed,
            veto_reason=veto_reason,
        )

    def _infer_type(self, document: Document) -> str:
        from app.events.schemas import fallback_event_type, schema_for_keywords

        text = f"{document.title}\n{document.content}"
        schema = schema_for_keywords(text)
        return schema.event_type if schema else fallback_event_type(text)

    def _entity_overlap(self, document: Document, candidate: Event) -> float:
        if not candidate.entity_ids:
            return 0.0
        document_text = f"{document.title}\n{document.content}"
        overlap = sum(1 for code in candidate.entity_ids if code in document_text)
        return min(1.0, overlap / max(1, len(candidate.entity_ids)))

    def _key_field_similarity_and_veto(
        self, candidate: Event, candidate_key_fields: dict[str, Any]
    ) -> tuple[float, bool, Optional[str]]:
        if not candidate.key_fields or not candidate_key_fields:
            return 0.50, False, None

        veto = self._check_veto(candidate, candidate_key_fields)
        if veto:
            return 0.0, True, veto

        shared = 0
        total = 0
        for key, value in candidate_key_fields.items():
            if key in ("currency", "unit"):
                continue
            total += 1
            existing = candidate.key_fields.get(key)
            if existing is not None and existing == value:
                shared += 1
        similarity = shared / total if total else 0.50
        return similarity, False, None

    def _check_veto(self, candidate: Event, candidate_key_fields: dict[str, Any]) -> Optional[str]:
        schema = EVENT_SCHEMAS.get(candidate.event_type)
        if schema is None:
            return None
        # 业绩期间冲突否决
        if "period" in schema.required_fields:
            existing_period = candidate.key_fields.get("period")
            new_period = candidate_key_fields.get("period")
            if existing_period and new_period and existing_period != new_period:
                return f"period_conflict: {existing_period} vs {new_period}"
        # 合同对手方冲突否决
        if "counterparties" in schema.required_fields:
            existing = candidate.key_fields.get("counterparties")
            new = candidate_key_fields.get("counterparties")
            if existing and new and existing != new:
                return f"counterparties_conflict: {existing} vs {new}"
        return None

    def _time_proximity(self, document: Document, candidate: Event) -> float:
        if not document.published_at or not candidate.occurred_at:
            return 0.0
        delta = abs((document.published_at - candidate.occurred_at).total_seconds())
        days = delta / 86400
        if days <= 1:
            return 1.0
        if days >= 30:
            return 0.0
        return round(1.0 - days / 30, 4)

    def _title_similarity(self, document: Document, candidate: Event) -> float:
        return round(SequenceMatcher(None, document.title, candidate.title).ratio(), 4)

    def _empty_features(self) -> MatchFeatures:
        return MatchFeatures(
            entity_overlap=0.0,
            type_compatibility=0.0,
            key_field_similarity=0.0,
            time_proximity=0.0,
            title_similarity=0.0,
        )
