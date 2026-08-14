"""Hybrid Retrieval Query Planner。

基于规则解析查询中的实体、时间范围、事件类型与意图，生成可审计的
RetrievalPlan。MVP 不依赖 LLM，所有规则必须可解释、可重放。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.platform.repository import Repository


@dataclass(frozen=True)
class RetrievalIntent:
    """单个检索意图。"""

    intent: str = "document_search"  # event_lookup | document_search | impact_analysis | timeline
    entity_ids: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)
    time_range: tuple[datetime | None, datetime | None] = (None, None)
    keywords: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RetrievalPlan:
    """检索计划：被 RetrievalService 执行并写入 RetrievalTrace。"""

    query: str
    intents: list[RetrievalIntent] = field(default_factory=list)
    backends: list[str] = field(default_factory=list)
    primary_backend: str = "vector"
    top_k: int = 10
    as_of: datetime | None = None
    # 预留策略字段
    profile_id: str | None = None
    max_cost_usd: float | None = None
    max_latency_ms: int | None = None


class QueryPlanner:
    """规则化 Query Planner。"""

    _EVENT_TYPE_KEYWORDS: dict[str, list[str]] = {
        "macro_policy": ["加息", "降息", "FOMC", "LPR", "准备金", "利率", "央行", "美联储"],
        "ma": ["收购", "合并", "并购", "重组", " takeover"],
        "earnings": ["财报", "业绩", "营收", "净利润", "EPS", "季报"],
        "dividend": ["分红", "派息", "股息", "送股"],
        "regulatory": ["监管", "处罚", "立案", "问询", "批复"],
    }

    _INTENT_KEYWORDS: dict[str, list[str]] = {
        "impact_analysis": ["影响", "利好", "利空", "板块", "传导", "受益", "承压"],
        "timeline": ["最新", "最近", "时间线", "timeline", "发生了什么"],
        "event_lookup": ["事件", "公告", "新闻"],
        "document_search": ["文档", "原文", "摘录", "chunk"],
    }

    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self._entity_index: dict[str, str] | None = None

    def plan(
        self,
        query: str,
        top_k: int = 10,
        as_of: datetime | None = None,
    ) -> RetrievalPlan:
        """为自然语言查询生成检索计划。"""
        time_range = self._extract_time_range(query, as_of)
        event_types = self._extract_event_types(query)
        entity_ids = self._resolve_entities(query)
        intent = self._classify_intent(query)

        intent_obj = RetrievalIntent(
            intent=intent,
            entity_ids=entity_ids,
            event_types=event_types,
            time_range=time_range,
            keywords=self._extract_keywords(query),
        )

        backends, primary = self._choose_backends(intent, bool(entity_ids))
        return RetrievalPlan(
            query=query,
            intents=[intent_obj],
            backends=backends,
            primary_backend=primary,
            top_k=top_k,
            as_of=as_of,
        )

    def _entity_index_map(self) -> dict[str, str]:
        if self._entity_index is None:
            index: dict[str, str] = {}
            for event in self.repository.list_events(limit=10_000):
                for entity_id in event.entity_ids:
                    entity = self.repository.get_entity(entity_id)
                    if entity is None:
                        continue
                    aliases = getattr(entity, "aliases", None) or []
                    for name in [entity.canonical_name, *aliases]:
                        if name:
                            index[name.lower()] = entity.id
            self._entity_index = index
        return self._entity_index

    def _resolve_entities(self, query: str) -> list[str]:
        """用最长匹配从已知实体别名中解析实体 id。"""
        lower = query.lower()
        index = self._entity_index_map()
        matched: set[str] = set()
        # 按名称长度降序，优先匹配长实体名
        for name, entity_id in sorted(index.items(), key=lambda x: -len(x[0])):
            if name in lower:
                matched.add(entity_id)
        return list(matched)

    def _extract_time_range(
        self, query: str, as_of: datetime | None
    ) -> tuple[datetime | None, datetime | None]:
        """解析时间窗。MVP 支持 ISO 日期与'最近N天'。"""
        anchor = as_of or datetime.now(timezone.utc)

        # 最近 N 天
        m = re.search(r"最近\s*(\d+)\s*天", query)
        if m:
            days = int(m.group(1))
            start = anchor - timedelta(days=days)
            return (start.replace(hour=0, minute=0, second=0, microsecond=0), anchor)

        # YYYY-MM-DD
        dates = re.findall(r"(\d{4}-\d{2}-\d{2})", query)
        if len(dates) >= 2:
            return (
                datetime.fromisoformat(dates[0]).replace(tzinfo=timezone.utc),
                datetime.fromisoformat(dates[1]).replace(tzinfo=timezone.utc),
            )
        if len(dates) == 1:
            dt = datetime.fromisoformat(dates[0]).replace(tzinfo=timezone.utc)
            return (
                dt.replace(hour=0, minute=0, second=0),
                dt.replace(hour=23, minute=59, second=59),
            )

        return (None, None)

    def _extract_event_types(self, query: str) -> list[str]:
        lower = query.lower()
        types: list[str] = []
        for event_type, keywords in self._EVENT_TYPE_KEYWORDS.items():
            if any(kw.lower() in lower for kw in keywords):
                types.append(event_type)
        return types

    def _classify_intent(self, query: str) -> str:
        lower = query.lower()
        scores: dict[str, int] = {}
        for intent, keywords in self._INTENT_KEYWORDS.items():
            scores[intent] = sum(1 for kw in keywords if kw.lower() in lower)
        if not scores or max(scores.values()) == 0:
            return "event_lookup"
        return max(scores, key=scores.get)

    def _extract_keywords(self, query: str) -> list[str]:
        """简单分词：保留长度 >=2 的中文字符与英文单词。"""
        tokens = re.findall(r"[a-zA-Z]+|[\u4e00-\u9fa5]{2,}", query)
        return [t for t in tokens if len(t) >= 2]

    def _choose_backends(
        self, intent: str, has_entities: bool
    ) -> tuple[list[str], str]:
        """根据意图选择后端列表与主后端。"""
        if intent == "impact_analysis":
            if has_entities:
                return (["graph", "vector", "sql"], "graph")
            return (["vector", "sql"], "vector")
        if intent == "timeline":
            return (["timeseries", "sql"], "timeseries")
        if intent == "document_search":
            return (["hybrid"], "hybrid")
        # event_lookup default
        if has_entities:
            return (["sql", "vector"], "sql")
        return (["vector", "hybrid"], "hybrid")
