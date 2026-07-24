"""实体对齐。

EntityResolver 把候选证券代码和公司名称映射到稳定 Entity/Security ID，
按 DD-20 §5 评分与决策：
- 代码精确匹配 1.0；全称匹配 0.90；简称匹配 0.75；来源主体匹配 0.95。
- 唯一候选 >= 0.90：自动对齐。
- 第一候选 < 0.90 或前两名差值 < 0.10：进入人工审核。
- 无候选：保留文本候选并标记 unresolved，不创建临时公司主数据。

主数据缺失时按已解析的 market_code 自动创建 Entity + Security，使后续可被引用；
名称候选不自动建公司主数据（避免污染主数据）。
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.domain import (
    Entity,
    EntityLink,
    EntityResolution,
    Security,
)
from app.events.reference_data import ReferenceDataProvider, ReferenceMatch
from app.platform.ids import new_id
from app.platform.repository import Repository

SECURITY_CODE = re.compile(r"(?<!\d)([036]\d{5})(?:\.(SZ|SH))?(?!\d)", re.IGNORECASE)

AUTO_THRESHOLD = 0.90
AMBIGUOUS_GAP = 0.10

REFERENCE_CONFIDENCE = {
    "code_exact": 1.0,
    "historical_code": 0.95,
    "name_full": 0.90,
    "historical_name": 0.90,
    "name_short": 0.75,
}


@dataclass(frozen=True)
class ResolvedEntity(EntityResolution):
    """Compatible resolution enriched with the stable security ID."""

    security_id: str | None = None


class EntityResolver:
    """将文档中的证券代码与公司名候选映射到稳定实体 ID。"""

    def __init__(
        self,
        repository: Repository,
        reference_data: ReferenceDataProvider | None = None,
    ) -> None:
        self.repository = repository
        self.reference_data = reference_data

    def resolve(
        self,
        document_text: str,
        document_id: str,
        as_of: datetime | None = None,
    ) -> list[EntityResolution]:
        if self.reference_data is not None:
            return self._resolve_reference_data(
                document_text,
                document_id,
                as_of or datetime.now(timezone.utc),
            )

        results: list[EntityResolution] = []
        seen_codes: set[str] = set()
        for code, exchange in SECURITY_CODE.findall(document_text):
            inferred = exchange.upper() if exchange else ("SH" if code.startswith("6") else "SZ")
            market_code = f"{code}.{inferred}"
            if market_code in seen_codes:
                continue
            seen_codes.add(market_code)
            results.append(self._resolve_code(market_code, code, inferred, document_id))
        return results

    def _resolve_reference_data(
        self,
        document_text: str,
        document_id: str,
        as_of: datetime,
    ) -> list[EntityResolution]:
        del document_id  # Reserved for a future review-task adapter.
        matches = self.reference_data.find_matches(document_text, as_of)
        grouped: dict[tuple[int, int, str], list[ReferenceMatch]] = {}
        for match in matches:
            grouped.setdefault((match.start, match.end, match.matched_value), []).append(match)

        results: list[ResolvedEntity] = []
        for candidates in grouped.values():
            unique = {
                (candidate.entity_id, candidate.security_id): candidate
                for candidate in candidates
            }
            if len(unique) > 1:
                results.append(self._ambiguous_resolution(list(unique.values())))
                continue
            results.append(self._resolved_reference_match(next(iter(unique.values())), as_of))

        # An injected provider is authoritative. Unknown or not-yet-effective codes
        # remain unresolved instead of creating master data from future knowledge.
        matched_spans = {(match.start, match.end) for match in matches}
        for found in SECURITY_CODE.finditer(document_text):
            if (found.start(), found.end()) in matched_spans:
                continue
            ticker, exchange = found.groups()
            inferred = exchange.upper() if exchange else ("SH" if ticker.startswith("6") else "SZ")
            market_code = f"{ticker}.{inferred}"
            results.append(
                ResolvedEntity(
                    market_code=market_code,
                    entity_id=None,
                    canonical_name=market_code,
                    confidence=0.0,
                    resolution_method="unresolved",
                    ambiguous=False,
                    security_id=None,
                )
            )

        return self._deduplicate_reference_results(results)

    def _resolved_reference_match(
        self,
        match: ReferenceMatch,
        as_of: datetime,
    ) -> ResolvedEntity:
        self._materialize_reference_match(match, as_of)
        market_code = (
            match.matched_value
            if match.identifier_type in {"code_exact", "historical_code"}
            else match.market_code
        )
        return ResolvedEntity(
            market_code=market_code,
            entity_id=match.entity_id,
            canonical_name=match.canonical_name,
            confidence=REFERENCE_CONFIDENCE[match.identifier_type],
            resolution_method=match.identifier_type,
            ambiguous=False,
            security_id=match.security_id,
        )

    def _materialize_reference_match(self, match: ReferenceMatch, as_of: datetime) -> None:
        if self.repository.get_entity(match.entity_id) is None:
            self.repository.save_entity(
                Entity(
                    id=match.entity_id,
                    entity_type="company",
                    canonical_name=match.canonical_name,
                    status="active",
                    valid_from=as_of,
                )
            )
        if self.repository.get_security_by_market_code(match.market_code) is None:
            ticker, exchange = match.market_code.split(".", maxsplit=1)
            self.repository.save_security(
                Security(
                    id=match.security_id,
                    entity_id=match.entity_id,
                    ticker=ticker,
                    exchange=exchange,
                    market_code=match.market_code,
                    valid_from=as_of,
                )
            )

    @staticmethod
    def _ambiguous_resolution(candidates: list[ReferenceMatch]) -> ResolvedEntity:
        first = sorted(
            candidates,
            key=lambda item: (item.entity_id, item.security_id, item.market_code),
        )[0]
        market_code = (
            first.matched_value
            if first.identifier_type in {"code_exact", "historical_code"}
            else first.market_code
        )
        return ResolvedEntity(
            market_code=market_code,
            entity_id=None,
            canonical_name=first.matched_value,
            confidence=REFERENCE_CONFIDENCE[first.identifier_type],
            resolution_method="ambiguous",
            ambiguous=True,
            security_id=None,
        )

    @staticmethod
    def _deduplicate_reference_results(
        results: list[ResolvedEntity],
    ) -> list[EntityResolution]:
        ordered = sorted(
            results,
            key=lambda item: (
                item.ambiguous,
                -item.confidence,
                item.market_code,
                item.entity_id or "",
            ),
        )
        deduplicated: list[EntityResolution] = []
        seen_entities: set[str] = set()
        seen_ambiguous: set[tuple[str, str]] = set()
        for result in ordered:
            if result.entity_id:
                if result.entity_id in seen_entities:
                    continue
                seen_entities.add(result.entity_id)
            elif result.ambiguous:
                key = (result.market_code, result.canonical_name)
                if key in seen_ambiguous:
                    continue
                seen_ambiguous.add(key)
            deduplicated.append(result)
        return deduplicated

    def _resolve_code(
        self,
        market_code: str,
        ticker: str,
        exchange: str,
        document_id: str,
    ) -> EntityResolution:
        security = self.repository.get_security_by_market_code(market_code)
        if security:
            entity = self.repository.get_entity(security.entity_id)
            return EntityResolution(
                market_code=market_code,
                entity_id=security.entity_id,
                canonical_name=entity.canonical_name if entity else market_code,
                confidence=1.0,
                resolution_method="code_exact",
                ambiguous=False,
            )

        # 主数据缺失：按 market_code 自动创建 Entity + Security（代码候选可信度高）。
        entity = Entity(
            id=new_id("ent"),
            entity_type="company",
            canonical_name=market_code,
            status="active",
            valid_from=datetime.now(timezone.utc),
        )
        self.repository.save_entity(entity)
        new_security = Security(
            id=new_id("sec"),
            entity_id=entity.id,
            ticker=ticker,
            exchange=exchange,
            market_code=market_code,
            valid_from=datetime.now(timezone.utc),
        )
        self.repository.save_security(new_security)
        return EntityResolution(
            market_code=market_code,
            entity_id=entity.id,
            canonical_name=market_code,
            confidence=1.0,
            resolution_method="code_exact_auto_created",
            ambiguous=False,
        )

    def to_links(self, resolutions: list[EntityResolution]) -> list[EntityLink]:
        """把解析结果转为事件-实体关联。歧义项不入关联，由审核任务承接。"""
        links: list[EntityLink] = []
        for resolution in resolutions:
            if resolution.ambiguous or not resolution.entity_id:
                continue
            links.append(
                EntityLink(
                    entity_id=resolution.entity_id,
                    market_code=resolution.market_code,
                    role="primary",
                    confidence=resolution.confidence,
                    resolution_method=resolution.resolution_method,
                )
            )
        return links

    def ambiguous_candidates(self, resolutions: list[EntityResolution]) -> list[EntityResolution]:
        return [resolution for resolution in resolutions if resolution.ambiguous]
