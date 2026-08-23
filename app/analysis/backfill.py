"""Deterministic repair of approved impact projections and aggregate snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.analysis.aggregation import ImpactAggregationService
from app.platform.repository import Repository


@dataclass(frozen=True)
class ImpactBackfillReport:
    as_of: datetime
    events_scanned: int
    approved_analyses: int
    contributions_before: int
    contributions_after: int
    contributions_created: int
    active_contributions: int
    expired_contributions: int
    future_contributions: int
    targets_recomputed: int
    snapshots_created: int


class ImpactProjectionBackfillService:
    """Repair derived impact state without changing approved research semantics."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.aggregation = ImpactAggregationService(repository)

    def run(self, *, as_of: datetime | None = None) -> ImpactBackfillReport:
        cutoff = as_of or datetime.now(timezone.utc)
        if cutoff.tzinfo is None:
            raise ValueError("IMPACT_BACKFILL_AS_OF_TIMEZONE_REQUIRED")
        events = self.repository.list_events(as_of=cutoff)
        before_ids = {item.id for item in self.repository.list_impact_contributions()}
        approved = 0
        projected_target_horizons: set[tuple[str, str]] = set()
        for event in events:
            analysis = self.repository.get_latest_impact_analysis_for_event(event.id)
            if (
                analysis is None
                or analysis.status != "approved"
                or (analysis.created_at is not None and analysis.created_at > cutoff)
            ):
                continue
            approved += 1
            for contribution in self.aggregation.project_analysis(analysis):
                projected_target_horizons.add((contribution.target_id, contribution.horizon))
        contributions = self.repository.list_impact_contributions()
        active = sum(
            (item.created_at is None or item.created_at <= cutoff)
            and (item.valid_from is None or item.valid_from <= cutoff)
            and (item.valid_to is None or cutoff <= item.valid_to)
            for item in contributions
        )
        expired = sum(
            item.valid_to is not None and item.valid_to < cutoff for item in contributions
        )
        future = sum(
            item.valid_from is not None and item.valid_from > cutoff for item in contributions
        )
        snapshots_created = 0
        targets_recomputed: set[str] = set()
        for target_id, horizon in sorted(projected_target_horizons):
            latest = self.repository.get_latest_target_impact_snapshot(
                target_id, horizon, "baseline", as_of=cutoff
            )
            if latest is not None and latest.as_of == cutoff:
                continue
            snapshot = self.aggregation.recompute_target(
                target_id, as_of=cutoff, horizon=horizon, persist=True
            )
            if snapshot is not None:
                snapshots_created += 1
                targets_recomputed.add(target_id)
        after_ids = {item.id for item in self.repository.list_impact_contributions()}
        return ImpactBackfillReport(
            as_of=cutoff,
            events_scanned=len(events),
            approved_analyses=approved,
            contributions_before=len(before_ids),
            contributions_after=len(after_ids),
            contributions_created=len(after_ids - before_ids),
            active_contributions=active,
            expired_contributions=expired,
            future_contributions=future,
            targets_recomputed=len(targets_recomputed),
            snapshots_created=snapshots_created,
        )
