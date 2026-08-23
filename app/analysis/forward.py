"""未来行业影响窗口计算。"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.domain import (
    ForwardCatalyst,
    ForwardImpactContribution,
    ForwardImpactPoint,
    ForwardImpactWindow,
)
from app.platform.ids import new_id
from app.platform.repository import Repository

RULE_VERSION = "forward-impact-v1"
ALLOWED_KINDS = {"scheduled", "conditional", "hypothetical"}


class ForwardImpactService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def create_window(self, window: ForwardImpactWindow) -> ForwardImpactWindow:
        self._validate_window(window)
        self.repository.save_forward_impact_window(window)
        return window

    def list_calendar_events(
        self,
        *,
        start: datetime,
        end: datetime,
        target_id: str | None = None,
        event_type: str | None = None,
        kinds: set[str] | None = None,
        include_candidates: bool = False,
        as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return the compatibility calendar projection over ForwardCatalyst.

        The normalized FutureEvent tables will replace this projection in the
        next migration; keeping the projection here lets the calendar ship
        without inventing a second source of truth.
        """
        as_of = as_of or datetime.now(timezone.utc)
        normalized = self._list_normalized_calendar_events(
            start=start,
            end=end,
            target_id=target_id,
            event_type=event_type,
            kinds=kinds,
            include_candidates=include_candidates,
            as_of=as_of,
        )
        if normalized:
            return normalized
        catalysts = self.repository.list_forward_catalysts(target_id)
        result: list[dict[str, Any]] = []
        for catalyst in catalysts:
            if catalyst.created_at and catalyst.created_at > as_of:
                continue
            if event_type and catalyst.event_type != event_type:
                continue
            if kinds and catalyst.kind not in kinds:
                continue
            if not include_candidates and catalyst.status not in {"approved", "confirmed"}:
                continue
            scheduled_from = catalyst.scheduled_from
            scheduled_to = catalyst.scheduled_to or scheduled_from
            if scheduled_from is not None and scheduled_to is not None:
                if scheduled_to < start or scheduled_from > end:
                    continue
            target = self.repository.get_impact_target(catalyst.target_id)
            result.append(
                {
                    **catalyst.__dict__,
                    "target_name": target.canonical_name if target else catalyst.target_id,
                    "target_type": target.target_type if target else None,
                    "time_precision": "window" if catalyst.scheduled_to else "date",
                    "direction": catalyst.trigger_definition.get("direction", "uncertain"),
                    "magnitude": catalyst.trigger_definition.get("magnitude", "moderate"),
                    "importance": catalyst.trigger_definition.get("importance", 0.5),
                }
            )
        return sorted(
            result,
            key=lambda item: (
                item.get("scheduled_from") is None,
                item.get("scheduled_from") or datetime.max.replace(tzinfo=timezone.utc),
            ),
        )

    def _list_normalized_calendar_events(
        self,
        *,
        start: datetime,
        end: datetime,
        target_id: str | None,
        event_type: str | None,
        kinds: set[str] | None,
        include_candidates: bool,
        as_of: datetime,
    ) -> list[dict[str, Any]]:
        events = self.repository.list_future_events()
        if not events:
            return []
        result: list[dict[str, Any]] = []
        for event in events:
            if event.created_at and event.created_at > as_of:
                continue
            if event_type and event.event_type != event_type:
                continue
            if kinds and event.kind not in kinds:
                continue
            revision = (
                self.repository.get_future_event_revision(event.current_revision_id)
                if event.current_revision_id
                else None
            )
            if revision is None or (
                revision.available_at and revision.available_at > as_of
            ):
                continue
            if not include_candidates and revision.status not in {"approved", "confirmed"}:
                continue
            scheduled_to = revision.scheduled_to or revision.scheduled_from
            if revision.scheduled_from and scheduled_to and (
                scheduled_to < start or revision.scheduled_from > end
            ):
                continue
            impacts = self.repository.list_future_event_target_impacts(
                event_id=event.id, target_id=target_id
            )
            if not impacts:
                impacts = [None]
            for impact in impacts:
                target = (
                    self.repository.get_impact_target(impact.target_id)
                    if impact
                    else None
                )
                result.append(
                    {
                        "id": event.id,
                        "revision_id": revision.id,
                        "target_id": impact.target_id if impact else target_id,
                        "target_name": target.canonical_name if target else None,
                        "target_type": target.target_type if target else None,
                        "kind": event.kind,
                        "title": revision.title,
                        "event_type": event.event_type,
                        "scheduled_from": revision.scheduled_from,
                        "scheduled_to": revision.scheduled_to,
                        "time_precision": revision.time_precision,
                        "status": revision.status,
                        "importance": revision.importance,
                        "probability_base": revision.probability_base,
                        "direction": impact.direction if impact else "uncertain",
                        "magnitude": impact.magnitude if impact else "uncertain",
                        "trigger_definition": {
                            "strength": impact.conditional_strength if impact else 0.0,
                            "confidence": impact.confidence if impact else 0.0,
                            "rationale": impact.rationale if impact else "",
                        },
                        "evidence_refs": revision.evidence_refs,
                        "source_url": revision.source_url,
                    }
                )
        return sorted(
            result,
            key=lambda item: (
                item.get("scheduled_from") is None,
                item.get("scheduled_from") or datetime.max.replace(tzinfo=timezone.utc),
            ),
        )

    def calendar_summary(
        self,
        *,
        start: datetime,
        end: datetime,
        timezone_name: str = "Asia/Shanghai",
        target_id: str | None = None,
        as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        tz = ZoneInfo(timezone_name)
        events = self.list_calendar_events(start=start, end=end, target_id=target_id, as_of=as_of)
        cursor = start.astimezone(tz).date()
        last = end.astimezone(tz).date()
        by_date: dict[date, list[dict[str, Any]]] = {}
        for event in events:
            event_start = event.get("scheduled_from")
            event_end = event.get("scheduled_to") or event_start
            if event_start is None:
                continue
            left = max(cursor, event_start.astimezone(tz).date())
            right = min(last, (event_end or event_start).astimezone(tz).date())
            while left <= right:
                by_date.setdefault(left, []).append(event)
                left += timedelta(days=1)
        result = []
        while cursor <= last:
            items = by_date.get(cursor, [])
            positive = sum(
                float(item.get("importance") or 0)
                for item in items
                if item["direction"] == "positive"
            )
            negative = sum(
                float(item.get("importance") or 0)
                for item in items
                if item["direction"] == "negative"
            )
            result.append({
                "date": cursor.isoformat(),
                "event_count": len(items),
                "event_previews": [
                    {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "target_name": item.get("target_name"),
                        "event_type": item.get("event_type"),
                        "scheduled_from": item.get("scheduled_from"),
                        "time_precision": item.get("time_precision"),
                        "status": item.get("status"),
                        "importance": item.get("importance", 0.0),
                        "direction": item.get("direction", "uncertain"),
                    }
                    for item in sorted(
                        items,
                        key=lambda item: (
                            -float(item.get("importance") or 0),
                            item.get("scheduled_from") is None,
                            item.get("scheduled_from") or datetime.max.replace(tzinfo=timezone.utc),
                        ),
                    )[:3]
                ],
                "hidden_event_count": max(0, len(items) - 3),
                "uncertain_time_count": sum(
                    1 for item in items if item.get("time_precision") in {"unknown", "window"}
                ),
                "major_event_count": sum(
                    1 for item in items if float(item.get("importance") or 0) >= 0.7
                ),
                "positive_strength": round(positive, 4),
                "negative_strength": round(negative, 4),
                "net_strength": round(positive - negative, 4),
                "direction": self._direction(positive - negative),
                "has_conflict": positive > 0 and negative > 0,
            })
            cursor += timedelta(days=1)
        return result

    def day_view(
        self,
        *,
        selected_date: date,
        timezone_name: str = "Asia/Shanghai",
        target_id: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        tz = ZoneInfo(timezone_name)
        start = datetime.combine(selected_date, time.min, tzinfo=tz).astimezone(timezone.utc)
        end = datetime.combine(selected_date, time.max, tzinfo=tz).astimezone(timezone.utc)
        events = self.list_calendar_events(start=start, end=end, target_id=target_id, as_of=as_of)
        active: list[dict[str, Any]] = []
        for item in events:
            definition = item.get("trigger_definition") or {}
            active.append({
                "catalyst_id": item["id"],
                "target_id": item["target_id"],
                "target_name": item["target_name"],
                "event_title": item["title"],
                "direction": item["direction"],
                "magnitude": item["magnitude"],
                "conditional_strength": definition.get("strength", 0.0),
                "occurrence_probability": item.get("probability_base"),
                "rationale": definition.get("rationale", ""),
            })
        return {
            "date": selected_date.isoformat(),
            "timezone": timezone_name,
            "scheduled_events": events,
            "active_impacts": active,
            "target_summary": self._target_summary(active),
        }

    def _target_summary(self, impacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in impacts:
            grouped.setdefault(item["target_id"], []).append(item)
        result = []
        for target_id, items in grouped.items():
            positive = sum(
                float(item["conditional_strength"] or 0)
                for item in items
                if item["direction"] == "positive"
            )
            negative = sum(
                float(item["conditional_strength"] or 0)
                for item in items
                if item["direction"] == "negative"
            )
            result.append({
                "target_id": target_id,
                "target_name": items[0]["target_name"],
                "positive_strength": round(positive, 4),
                "negative_strength": round(negative, 4),
                "net_strength": round(positive - negative, 4),
                "direction": self._direction(positive - negative),
                "event_count": len(items),
            })
        return sorted(result, key=lambda item: abs(item["net_strength"]), reverse=True)

    def recompute(self, window_id: str) -> list[ForwardImpactPoint]:
        window = self.repository.get_forward_impact_window(window_id)
        if window is None:
            raise ValueError("FORWARD_IMPACT_WINDOW_NOT_FOUND")
        catalysts = self.repository.list_forward_catalysts(window.target_id)
        catalysts = [item for item in catalysts if self._included(item, window)]
        contributions = self._build_contributions(window, catalysts)
        for contribution in contributions:
            self.repository.save_forward_contribution(contribution)
        points = self._build_points(window, contributions)
        self.repository.save_forward_points(points)
        updated = ForwardImpactWindow(
            **{**window.__dict__, "status": "ready", "rule_version": RULE_VERSION}
        )
        self.repository.save_forward_impact_window(updated)
        return points

    def graph(self, window_id: str) -> dict[str, Any]:
        window = self.repository.get_forward_impact_window(window_id)
        if window is None:
            raise ValueError("FORWARD_IMPACT_WINDOW_NOT_FOUND")
        target = self.repository.get_impact_target(window.target_id)
        nodes = [
            {
                "node_id": f"target_{window.target_id}",
                "node_type": "impact",
                "label": target.canonical_name if target else window.target_id,
                "layer": 4,
            }
        ]
        edges = []
        for item in self.repository.list_forward_contributions(window_id):
            catalyst = self.repository.get_forward_catalyst(item.catalyst_id)
            node_id = f"catalyst_{item.catalyst_id}"
            nodes.append(
                {
                    "node_id": node_id,
                    "node_type": "event",
                    "label": catalyst.title if catalyst else item.catalyst_id,
                    "layer": 0,
                    "group": catalyst.kind if catalyst else "future",
                }
            )
            edges.append(
                {
                    "edge_id": f"forward_{item.id}",
                    "source_node_id": node_id,
                    "target_node_id": f"target_{window.target_id}",
                    "mechanism": "未来催化剂传导",
                    "direction": item.direction,
                    "order": "first_order",
                    "horizon": "unknown",
                    "inference_kind": "inference",
                    "confidence": item.confidence,
                    "evidence_refs": [],
                }
            )
        return {"nodes": nodes, "edges": edges}

    def _build_contributions(
        self, window: ForwardImpactWindow, catalysts: list[ForwardCatalyst]
    ) -> list[ForwardImpactContribution]:
        result = []
        for catalyst in catalysts:
            probability = 1.0 if catalyst.kind == "scheduled" else catalyst.probability_base
            if catalyst.kind == "conditional" and probability is None:
                continue
            if catalyst.kind == "hypothetical":
                probability = None
            onset = catalyst.scheduled_from or window.window_start
            peak = onset + timedelta(days=14)
            valid_to = catalyst.scheduled_to or onset + timedelta(days=90)
            # The initial path is explicit and reviewable; a future LLM path can replace it.
            direction = str(catalyst.trigger_definition.get("direction", "uncertain"))
            magnitude = str(catalyst.trigger_definition.get("magnitude", "moderate"))
            strength = float(catalyst.trigger_definition.get("strength", 0.5))
            result.append(
                ForwardImpactContribution(
                    id=new_id("fic"),
                    window_id=window.id,
                    catalyst_id=catalyst.id,
                    target_id=window.target_id,
                    scenario_id="stress" if catalyst.kind == "hypothetical" else "baseline",
                    direction=direction,
                    magnitude=magnitude,
                    conditional_strength=max(0.0, min(1.0, strength)),
                    occurrence_probability=probability,
                    expected_strength=None
                    if probability is None
                    else round(strength * probability, 6),
                    confidence=float(catalyst.trigger_definition.get("confidence", 0.5)),
                    onset_at=onset,
                    expected_peak_at=peak,
                    valid_to=valid_to,
                    causal_edge_refs=list(catalyst.trigger_definition.get("causal_edge_refs", [])),
                    created_at=datetime.now(timezone.utc),
                )
            )
        return result

    def _build_points(
        self, window: ForwardImpactWindow, contributions: list[ForwardImpactContribution]
    ) -> list[ForwardImpactPoint]:
        step = self._step(window)
        points: list[ForwardImpactPoint] = []
        cursor = window.window_start
        while cursor <= window.window_end:
            for scenario in ("baseline", "stress"):
                active = [
                    item
                    for item in contributions
                    if item.scenario_id == scenario and self._active(item, cursor)
                ]
                positive_c = [
                    self._strength(item, cursor, expected=False)
                    for item in active
                    if item.direction == "positive"
                ]
                negative_c = [
                    self._strength(item, cursor, expected=False)
                    for item in active
                    if item.direction == "negative"
                ]
                positive_e = [
                    self._strength(item, cursor, expected=True)
                    for item in active
                    if item.direction == "positive" and item.expected_strength is not None
                ]
                negative_e = [
                    self._strength(item, cursor, expected=True)
                    for item in active
                    if item.direction == "negative" and item.expected_strength is not None
                ]
                pc, nc = self._gross(positive_c), self._gross(negative_c)
                pe = self._gross(positive_e) if positive_e else None
                ne = self._gross(negative_e) if negative_e else None
                points.append(
                    ForwardImpactPoint(
                        id=new_id("fip"),
                        window_id=window.id,
                        point_at=cursor,
                        scenario_id=scenario,
                        positive_conditional=round(pc, 6),
                        negative_conditional=round(nc, 6),
                        net_conditional=round(pc - nc, 6),
                        positive_expected=None if pe is None else round(pe, 6),
                        negative_expected=None if ne is None else round(ne, 6),
                        net_expected=None if pe is None or ne is None else round(pe - ne, 6),
                        direction=self._direction(pc - nc),
                        confidence=round(sum(item.confidence for item in active) / len(active), 6)
                        if active
                        else 0.0,
                        dominant_catalyst_id=max(
                            active,
                            key=lambda item: self._strength(item, cursor, expected=False),
                            default=None,
                        ).catalyst_id
                        if active
                        else None,
                    )
                )
            cursor += step
        return points

    def _included(self, catalyst: ForwardCatalyst, window: ForwardImpactWindow) -> bool:
        if catalyst.kind not in ALLOWED_KINDS or catalyst.kind not in window.included_kinds:
            return False
        if window.catalyst_ids and catalyst.id not in window.catalyst_ids:
            return False
        if window.event_types and catalyst.event_type not in window.event_types:
            return False
        if catalyst.status != "approved":
            return False
        if catalyst.kind == "hypothetical" and window.scenario_set_id == "baseline":
            return False
        return True

    def _active(self, item: ForwardImpactContribution, point: datetime) -> bool:
        return (item.onset_at is None or point >= item.onset_at) and (
            item.valid_to is None or point <= item.valid_to
        )

    def _strength(
        self, item: ForwardImpactContribution, point: datetime, *, expected: bool
    ) -> float:
        base = item.expected_strength if expected else item.conditional_strength
        if base is None:
            return 0.0
        onset = item.onset_at or point
        peak = item.expected_peak_at or onset
        end = item.valid_to or peak
        if point <= peak:
            progress = (point - onset).total_seconds() / max((peak - onset).total_seconds(), 1)
            return max(0.0, min(1.0, base * progress))
        decay = (end - point).total_seconds() / max((end - peak).total_seconds(), 1)
        return max(0.0, min(1.0, base * decay))

    def _gross(self, values: list[float]) -> float:
        return 1 - math.prod(1 - min(1.0, value) for value in values) if values else 0.0

    def _direction(self, net: float) -> str:
        if net >= 0.15:
            return "positive"
        if net <= -0.15:
            return "negative"
        return "mixed" if abs(net) < 0.05 else "uncertain"

    def _step(self, window: ForwardImpactWindow) -> timedelta:
        if window.granularity == "day" or (
            window.granularity == "auto" and (window.window_end - window.window_start).days <= 31
        ):
            return timedelta(days=1)
        if window.granularity == "month" or (
            window.granularity == "auto" and (window.window_end - window.window_start).days > 180
        ):
            return timedelta(days=30)
        return timedelta(days=7)

    def _validate_window(self, window: ForwardImpactWindow) -> None:
        if (
            window.window_start.tzinfo is None
            or window.window_end.tzinfo is None
            or window.as_of.tzinfo is None
        ):
            raise ValueError("FORWARD_WINDOW_TIMEZONE_REQUIRED")
        if window.window_start < window.as_of:
            raise ValueError("FORWARD_WINDOW_START_BEFORE_AS_OF")
        if (
            window.window_end <= window.window_start
            or (window.window_end - window.window_start).days > 730
        ):
            raise ValueError("FORWARD_WINDOW_RANGE_INVALID")
