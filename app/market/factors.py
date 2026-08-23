"""Point-in-time forecast factors derived from approved research outputs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from app.analysis.aggregation import ImpactAggregationService
from app.domain import ImpactTargetDefinition, TargetImpactSnapshot
from app.market.provider import MarketInstrument
from app.platform.repository import Repository

FACTOR_RULE_VERSION = "forecast-factor-v1"
HORIZON_CANDIDATES = {
    1: ("0_1d", "2_5d", "unknown"),
    3: ("2_5d", "0_1d", "1_4w", "unknown"),
    5: ("2_5d", "1_4w", "0_1d", "unknown"),
    20: ("1_4w", "1_4q", "2_5d", "unknown"),
}


@dataclass(frozen=True)
class FactorSource:
    target_id: str
    target_name: str
    target_type: str
    snapshot_id: str
    snapshot_as_of: datetime
    snapshot_horizon: str
    net_score: float
    confidence: float
    dominant_event_id: str | None
    source_hash: str
    match_kind: str
    match_weight: float


@dataclass(frozen=True)
class ForecastFactorSnapshot:
    factor: str
    instrument_id: str
    as_of: datetime
    horizon: int
    status: str
    score: float | None
    confidence: float
    reason: str | None
    sources: tuple[FactorSource, ...]
    source_hash: str
    rule_version: str = FACTOR_RULE_VERSION


class EventImpactFactorService:
    """Resolve research targets to an instrument and build a point-in-time signal.

    Only approved, time-valid target mappings participate in a forecast. Name
    matching is deliberately excluded from this service and is handled by the
    governance workflow as a proposed mapping requiring reviewer approval.
    """

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def snapshot(
        self, instrument: MarketInstrument, *, as_of: datetime, horizon: int
    ) -> ForecastFactorSnapshot:
        if horizon not in HORIZON_CANDIDATES:
            raise ValueError(f"unsupported factor horizon: {horizon}")
        matched = self._matched_targets(instrument, as_of)
        if not matched:
            return self._unavailable(instrument, as_of, horizon, "impact_target_not_mapped")

        sources: list[FactorSource] = []
        aggregation = ImpactAggregationService(self.repository)
        for target, match_kind, match_weight in matched:
            snapshot = self._best_snapshot(aggregation, target.id, as_of, horizon)
            if snapshot is None or snapshot.confidence <= 0:
                continue
            sources.append(
                FactorSource(
                    target_id=target.id,
                    target_name=target.canonical_name,
                    target_type=target.target_type,
                    snapshot_id=snapshot.id,
                    snapshot_as_of=snapshot.as_of,
                    snapshot_horizon=snapshot.horizon,
                    net_score=snapshot.net_score,
                    confidence=snapshot.confidence,
                    dominant_event_id=snapshot.dominant_event_id,
                    source_hash=snapshot.source_hash,
                    match_kind=match_kind,
                    match_weight=match_weight,
                )
            )
        if not sources:
            return self._unavailable(instrument, as_of, horizon, "approved_impact_snapshot_missing")

        denominator = sum(item.match_weight for item in sources)
        score = (
            sum(item.net_score * item.confidence * item.match_weight for item in sources)
            / denominator
        )
        confidence = 1.0
        for item in sources:
            confidence *= 1.0 - min(0.99, item.confidence * item.match_weight)
        digest = hashlib.sha256(
            "|".join(sorted(f"{item.target_id}:{item.source_hash}" for item in sources)).encode()
        ).hexdigest()
        return ForecastFactorSnapshot(
            factor="event_impact",
            instrument_id=instrument.id,
            as_of=as_of,
            horizon=horizon,
            status="available",
            score=round(max(-1.0, min(1.0, score)), 6),
            confidence=round(1.0 - confidence, 6),
            reason=None,
            sources=tuple(sources),
            source_hash=digest,
        )

    def _best_snapshot(
        self,
        aggregation: ImpactAggregationService,
        target_id: str,
        as_of: datetime,
        horizon: int,
    ) -> TargetImpactSnapshot | None:
        for candidate in HORIZON_CANDIDATES[horizon]:
            snapshot = aggregation.recompute_target(
                target_id,
                as_of=as_of,
                horizon=candidate,
                scenario_set_id="baseline",
                persist=False,
            )
            if snapshot is not None and snapshot.confidence > 0:
                return snapshot
        return None

    def _matched_targets(
        self, instrument: MarketInstrument, as_of: datetime
    ) -> list[tuple[ImpactTargetDefinition, str, float]]:
        memberships = {
            item.industry_code: item
            for item in self.repository.list_instrument_industry_memberships(
                instrument.id, "approved"
            )
            if _known_and_valid_at(item.created_at, item.valid_from, item.valid_to, as_of)
        }
        matches: list[tuple[ImpactTargetDefinition, str, float]] = []
        for mapping in self.repository.list_impact_target_mappings(status="approved"):
            if not _known_and_valid_at(
                mapping.created_at, mapping.valid_from, mapping.valid_to, as_of
            ):
                continue
            if mapping.reviewed_at is not None and mapping.reviewed_at > as_of:
                continue
            target = self.repository.get_impact_target(mapping.target_id)
            if target is None or not _valid_at(target.valid_from, target.valid_to, as_of):
                continue
            match_weight: float | None = None
            if mapping.mapping_type == "instrument" and mapping.mapping_code == instrument.id:
                match_weight = mapping.weight
            elif mapping.mapping_type == "market" and mapping.mapping_code == instrument.market:
                match_weight = mapping.weight
            elif mapping.mapping_type == "industry" and mapping.mapping_code in memberships:
                match_weight = mapping.weight * memberships[mapping.mapping_code].weight
            if match_weight is not None and match_weight > 0:
                matches.append(
                    (
                        target,
                        f"approved_{mapping.mapping_type}_mapping",
                        min(1.0, match_weight * mapping.confidence),
                    )
                )
        return sorted(matches, key=lambda item: (-item[2], item[0].id))

    def _unavailable(
        self, instrument: MarketInstrument, as_of: datetime, horizon: int, reason: str
    ) -> ForecastFactorSnapshot:
        return ForecastFactorSnapshot(
            factor="event_impact",
            instrument_id=instrument.id,
            as_of=as_of,
            horizon=horizon,
            status="unavailable",
            score=None,
            confidence=0.0,
            reason=reason,
            sources=(),
            source_hash=hashlib.sha256(
                f"{instrument.id}|{as_of.isoformat()}|{horizon}|{reason}".encode()
            ).hexdigest(),
        )


def _valid_at(valid_from: datetime | None, valid_to: datetime | None, as_of: datetime) -> bool:
    return (valid_from is None or valid_from <= as_of) and (valid_to is None or valid_to >= as_of)


def _known_and_valid_at(
    created_at: datetime | None,
    valid_from: datetime | None,
    valid_to: datetime | None,
    as_of: datetime,
) -> bool:
    return (created_at is None or created_at <= as_of) and _valid_at(valid_from, valid_to, as_of)
