"""跨事件目标影响聚合。

该模块只读取已批准的单事件分析，单事件因果图仍是事实来源；组合快照是
可重算的派生读模型，不会覆盖任何事件分析版本。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain import (
    ImpactAnalysis,
    ImpactContribution,
    ImpactDimensionContribution,
    TargetImpactSnapshot,
    TargetImpactSnapshotContribution,
)
from app.platform.ids import new_id
from app.platform.repository import Repository

RULE_VERSION = "impact-aggregation-v2"
MAGNITUDE_WEIGHT = {"strong": 1.0, "moderate": 0.65, "weak": 0.35, "uncertain": 0.2}
HORIZON_HALF_LIFE_DAYS = {
    "0_1d": 1.0,
    "2_5d": 3.0,
    "1_4w": 14.0,
    "1_4q": 90.0,
    "1y_plus": 365.0,
    "unknown": 30.0,
}


class ImpactAggregationService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def project_analysis(self, analysis: ImpactAnalysis) -> list[ImpactContribution]:
        """将一个 approved 分析投影为目标贡献，重复执行保持幂等。"""
        if analysis.status != "approved":
            return []
        event = self.repository.get_event(analysis.event_id)
        if event is None:
            return []
        payload = analysis.analysis_payload or {}
        assessments = payload.get("impact_assessments") or []
        if not assessments:
            assessments = [
                self._legacy_assessment(item, index) for index, item in enumerate(analysis.impacts)
            ]
        scenarios = {item.get("scenario_id") for item in assessments if item.get("scenario_id")}
        scenario_id = "scn_base" if "scn_base" in scenarios else (next(iter(scenarios), "baseline"))
        created: list[ImpactContribution] = []
        for index, assessment in enumerate(assessments):
            if assessment.get("scenario_id", scenario_id) != scenario_id:
                continue
            target = self._target_for_assessment(assessment)
            confidence = float(assessment.get("confidence", 0.0) or 0.0)
            path_confidence = self._path_confidence(payload, assessment)
            magnitude = str(assessment.get("magnitude", "uncertain"))
            direction = str(
                assessment.get("dimensions", [{}])[0].get(
                    "direction", assessment.get("direction", "uncertain")
                )
            )
            horizon = str(assessment.get("horizon", "unknown"))
            base = (
                MAGNITUDE_WEIGHT.get(magnitude, 0.2)
                * confidence
                * float(event.importance)
                * path_confidence
            )
            contribution_id = (
                f"ic_{analysis.id}_{assessment.get('assessment_id', f'legacy_{index}')}"
            )
            contribution = ImpactContribution(
                id=contribution_id,
                event_id=event.id,
                analysis_id=analysis.id,
                assessment_id=str(assessment.get("assessment_id", f"ia_legacy_{index}")),
                target_id=target.id,
                scenario_id=scenario_id,
                direction=direction,
                magnitude=magnitude,
                horizon=horizon,
                base_strength=round(max(0.0, min(1.0, base)), 6),
                effective_strength=round(max(0.0, min(1.0, base)), 6),
                event_importance=float(event.importance),
                assessment_confidence=confidence,
                path_confidence=path_confidence,
                target_role=str(assessment.get("target_role", "direct_subject")),
                relationship_id=assessment.get("relationship_id"),
                relationship_confidence=float(
                    assessment.get("relationship_confidence", 1.0) or 1.0
                ),
                inference_kind=str(assessment.get("inference_kind", "derived")),
                evidence_refs=assessment.get("evidence_refs") or [],
                conditions=assessment.get("conditions") or [],
                invalidation_conditions=assessment.get("invalidation_conditions") or [],
                publication_scope=str(assessment.get("publication_scope", "official")),
                valid_from=self._timing_date(assessment, "onset_at") or event.occurred_at,
                expected_peak_at=self._timing_date(assessment, "expected_peak_at")
                or event.occurred_at + timedelta(days=self._peak_days(horizon)),
                valid_to=self._timing_date(assessment, "valid_to")
                or event.occurred_at + timedelta(days=self._end_days(horizon)),
                rule_version=RULE_VERSION,
                # Projection may be repaired later, but the contribution became
                # knowable when its approved analysis was created. Persist that
                # knowledge time so a backfill cannot leak into historical as_of
                # replay merely because it ran today.
                created_at=analysis.created_at or datetime.now(timezone.utc),
            )
            self.repository.save_impact_contribution(contribution)
            dimensions = assessment.get("dimensions") or [
                {"dimension": "other", "direction": direction, "magnitude": magnitude}
            ]
            for dimension in dimensions:
                name = str(dimension.get("dimension", "other"))
                strength = float(dimension.get("strength", base) or base)
                self.repository.save_impact_dimension_contribution(
                    ImpactDimensionContribution(
                        id=f"idc_{contribution.id}_{name}",
                        contribution_id=contribution.id,
                        dimension=name,
                        direction=str(dimension.get("direction", direction)),
                        magnitude=str(dimension.get("magnitude", magnitude)),
                        base_strength=max(0.0, min(1.0, strength)),
                        effective_strength=max(0.0, min(1.0, strength)),
                        confidence=float(dimension.get("confidence", confidence) or confidence),
                        quantitative_range=dimension.get("quantitative_range"),
                        unit=dimension.get("unit"),
                        evidence_refs=dimension.get("evidence_refs") or [],
                    )
                )
            created.append(contribution)
        return created

    def recompute_target(
        self,
        target_id: str,
        *,
        as_of: datetime | None = None,
        horizon: str | None = None,
        scenario_set_id: str = "baseline",
        publication_scope: str = "official",
        persist: bool = True,
    ) -> TargetImpactSnapshot | None:
        as_of = as_of or datetime.now(timezone.utc)
        target = self.repository.get_impact_target(target_id)
        if target is None:
            return None
        target_scope = self._target_scope(target_id, as_of=as_of)
        contributions = [
            item
            for scoped_target in target_scope
            for item in self.repository.list_impact_contributions(scoped_target)
        ]
        # Filter by platform knowledge time before choosing the latest analysis
        # version. Otherwise a future version can hide the older version that
        # was actually visible at the requested replay cutoff.
        contributions = [
            item for item in contributions if item.created_at is None or item.created_at <= as_of
        ]
        approved_latest: dict[str, ImpactAnalysis] = {}
        for contribution in contributions:
            analysis = self.repository.get_impact_analysis(contribution.analysis_id)
            allowed_statuses = {"approved"} if publication_scope == "official" else {
                "draft", "needs_review", "approved"
            }
            if analysis is None or analysis.status not in allowed_statuses:
                continue
            current = approved_latest.get(analysis.event_id)
            if current is None or analysis.version > current.version:
                approved_latest[analysis.event_id] = analysis
        contributions = [
            item
            for item in contributions
            if item.analysis_id in {analysis.id for analysis in approved_latest.values()}
            and (
                item.publication_scope == publication_scope
                or publication_scope == "exploration"
            )
        ]
        if horizon:
            contributions = [item for item in contributions if item.horizon == horizon]
        contributions = [
            item
            for item in contributions
            if self._scenario_matches(item.scenario_id, scenario_set_id)
        ]
        contributions = [
            item
            for item in contributions
            if (item.valid_from is None or item.valid_from <= as_of)
            and (item.valid_to is None or as_of <= item.valid_to)
        ]
        relations = self.repository.list_event_impact_relations()
        relation_weights = self._relation_weights(relations)
        scored: list[tuple[ImpactContribution, float, float]] = []
        for item in contributions:
            age_days = max(0.0, (as_of - (item.valid_from or as_of)).total_seconds() / 86400)
            half_life = HORIZON_HALF_LIFE_DAYS.get(item.horizon, 30.0)
            time_weight = math.exp(-math.log(2) * age_days / half_life)
            dependency = relation_weights.get(item.event_id, 1.0)
            effective = max(
                0.0,
                min(
                    1.0,
                    item.base_strength
                    * time_weight
                    * dependency
                    * max(0.0, min(1.0, item.relationship_confidence))
                    * target_scope.get(item.target_id, 1.0),
                ),
            )
            if effective > 0:
                scored.append((item, effective, time_weight))
        positive = [score for item, score, _ in scored if item.direction == "positive"]
        negative = [score for item, score, _ in scored if item.direction == "negative"]
        mixed = [score / 2 for item, score, _ in scored if item.direction == "mixed"]
        positive.extend(mixed)
        negative.extend(mixed)
        positive_gross = (
            1 - math.prod(1 - min(1.0, value) for value in positive) if positive else 0.0
        )
        negative_gross = (
            1 - math.prod(1 - min(1.0, value) for value in negative) if negative else 0.0
        )
        net_score = round(positive_gross - negative_gross, 6)
        direction = self._direction(net_score, positive_gross, negative_gross)
        magnitude = self._magnitude(abs(net_score))
        conflict = min(positive_gross, negative_gross) / max(positive_gross, negative_gross, 1e-9)
        evidence_confidence = (
            1 - math.prod(1 - item.assessment_confidence * score for item, score, _ in scored)
            if scored
            else 0.0
        )
        confidence = round(max(0.0, min(0.99, evidence_confidence * (1 - 0.45 * conflict))), 6)
        dominant = max(scored, key=lambda item: item[1], default=None)
        previous = (
            self.repository.get_latest_target_impact_snapshot(target_id, horizon, scenario_set_id)
            if persist
            else None
        )
        source_hash = hashlib.sha256(
            "|".join(sorted(item.analysis_id for item, _, _ in scored)).encode()
        ).hexdigest()
        snapshot = TargetImpactSnapshot(
            id=new_id("tis"),
            target_id=target_id,
            as_of=as_of,
            horizon=horizon or "all",
            scenario_set_id=scenario_set_id,
            positive_gross=round(positive_gross, 6),
            negative_gross=round(negative_gross, 6),
            net_score=net_score,
            direction=direction,
            magnitude=magnitude,
            confidence=confidence,
            dominant_event_id=dominant[0].event_id if dominant else None,
            previous_direction=previous.direction if previous else None,
            change_type=self._change_type(previous.direction if previous else None, direction),
            source_hash=source_hash,
            rule_version=RULE_VERSION,
            explanation=self._explanation(
                target.canonical_name, direction, positive_gross, negative_gross
            ),
            created_at=datetime.now(timezone.utc),
        )
        total = sum(score for _, score, _ in scored) or 1.0
        links = [
            TargetImpactSnapshotContribution(
                snapshot.id,
                item.id,
                item.event_id,
                item.direction,
                round(score, 6),
                round(score / total, 6),
            )
            for item, score, _ in scored
        ]
        if persist:
            self.repository.save_target_impact_snapshot(snapshot, links)
        return snapshot

    def dashboard(
        self,
        target_id: str,
        *,
        as_of: datetime | None = None,
        horizon: str | None = None,
        scenario_set_id: str = "baseline",
        publication_scope: str = "official",
    ) -> dict[str, Any] | None:
        """Build the explainable target view without mutating the read model."""
        target = self.repository.get_impact_target(target_id)
        if target is None:
            return None
        as_of = as_of or datetime.now(timezone.utc)
        snapshot = self.recompute_target(
            target_id,
            as_of=as_of,
            horizon=horizon,
            scenario_set_id=scenario_set_id,
            publication_scope=publication_scope,
            persist=False,
        )
        if snapshot is None:
            return {"target": target.__dict__, "snapshot": None, "contributions": []}
        target_scope = self._target_scope(target_id, as_of=as_of)
        all_contributions = [
            item
            for scoped_target in target_scope
            for item in self.repository.list_impact_contributions(scoped_target)
        ]
        allowed_statuses = {"approved"} if publication_scope == "official" else {
            "draft", "needs_review", "approved"
        }
        approved_ids = {
            item.id
            for item in all_contributions
            if (analysis := self.repository.get_impact_analysis(item.analysis_id))
            and analysis.status in allowed_statuses
        }
        contributions = {
            item.id: item
            for item in all_contributions
            if item.id in approved_ids
            and (item.publication_scope == publication_scope or publication_scope == "exploration")
            and (item.created_at is None or item.created_at <= as_of)
            and (item.valid_from is None or item.valid_from <= as_of)
            and (horizon is None or item.horizon == horizon)
            and self._scenario_matches(item.scenario_id, scenario_set_id)
        }
        relations = self._relation_weights(self.repository.list_event_impact_relations())
        events: dict[str, Any] = {}
        enriched: list[dict[str, Any]] = []
        scored: list[tuple[ImpactContribution, float, float]] = []
        for contribution in contributions.values():
            age_days = max(
                0.0,
                (as_of - (contribution.valid_from or as_of)).total_seconds() / 86400,
            )
            half_life = HORIZON_HALF_LIFE_DAYS.get(contribution.horizon, 30.0)
            time_weight = math.exp(-math.log(2) * age_days / half_life)
            effective = max(
                0.0,
                min(
                    1.0,
                    contribution.base_strength
                    * time_weight
                    * relations.get(contribution.event_id, 1.0)
                    * max(0.0, min(1.0, contribution.relationship_confidence))
                    * target_scope.get(contribution.target_id, 1.0),
                ),
            )
            if contribution.valid_to is not None and as_of > contribution.valid_to:
                effective = 0.0
            scored.append((contribution, effective, time_weight))
        total = sum(item[1] for item in scored) or 1.0
        for contribution, effective, time_weight in scored:
            event = self.repository.get_event(contribution.event_id)
            analysis = self.repository.get_impact_analysis(contribution.analysis_id)
            event_data = (
                event.__dict__
                if event
                else {"id": contribution.event_id, "title": contribution.event_id}
            )
            events[contribution.event_id] = event_data
            rationale = ""
            if analysis:
                rationale = self._rationale_for_target(analysis, target)
            enriched.append(
                {
                    "contribution_id": contribution.id,
                    "event_id": contribution.event_id,
                    "direction": contribution.direction,
                    "effective_strength": round(effective, 6),
                    "contribution_share": round(effective / total, 6),
                    "event_title": event_data.get("title", contribution.event_id),
                    "event_occurred_at": event_data.get("occurred_at"),
                    "analysis_id": contribution.analysis_id,
                    "analysis_version": analysis.version if analysis else None,
                    "magnitude": contribution.magnitude,
                    "horizon": contribution.horizon,
                    "base_strength": contribution.base_strength,
                    "event_importance": contribution.event_importance,
                    "assessment_confidence": contribution.assessment_confidence,
                    "path_confidence": contribution.path_confidence,
                    "dependency_weight": relations.get(contribution.event_id, 1.0),
                    "target_role": contribution.target_role,
                    "relationship_confidence": contribution.relationship_confidence,
                    "inference_kind": contribution.inference_kind,
                    "publication_scope": contribution.publication_scope,
                    "evidence_refs": contribution.evidence_refs,
                    "conditions": contribution.conditions,
                    "invalidation_conditions": contribution.invalidation_conditions,
                    "time_weight": round(time_weight, 6),
                    "valid_from": contribution.valid_from,
                    "expected_peak_at": contribution.expected_peak_at,
                    "valid_to": contribution.valid_to,
                    "rationale": rationale,
                    "source_url": (event_data.get("key_fields") or {}).get("source_url"),
                }
            )
        enriched.sort(key=lambda item: item["effective_strength"], reverse=True)
        dimension_rows: dict[str, list[tuple[ImpactContribution, float]]] = {}
        time_weights = {item.id: weight for item, _, weight in scored}
        contribution_ids = {item.id for item in contributions.values()}
        for dimension_item in self.repository.list_impact_dimension_contributions():
            if dimension_item.contribution_id not in contribution_ids:
                continue
            parent = next(
                (
                    item
                    for item in contributions.values()
                    if item.id == dimension_item.contribution_id
                ),
                None,
            )
            if parent is None:
                continue
            dimension_rows.setdefault(dimension_item.dimension, []).append(
                (parent, dimension_item.effective_strength * time_weights.get(parent.id, 1.0))
            )
        dimensions: list[dict[str, Any]] = []
        for name, rows in dimension_rows.items():
            positive_values = [value for item, value in rows if item.direction == "positive"]
            negative_values = [value for item, value in rows if item.direction == "negative"]
            positive_gross = (
                1 - math.prod(1 - min(1.0, value) for value in positive_values)
                if positive_values
                else 0.0
            )
            negative_gross = (
                1 - math.prod(1 - min(1.0, value) for value in negative_values)
                if negative_values
                else 0.0
            )
            net = round(positive_gross - negative_gross, 6)
            dimensions.append({
                "dimension": name,
                "positive_gross": round(positive_gross, 6),
                "negative_gross": round(negative_gross, 6),
                "net_score": net,
                "direction": self._direction(net, positive_gross, negative_gross),
                "confidence": round(
                    sum(item.assessment_confidence for item, _ in rows) / len(rows), 6
                ),
            })
        return {
            "target": target.__dict__,
            "snapshot": snapshot.__dict__,
            "contributions": enriched,
            "events": list(events.values()),
            "dimensions": sorted(dimensions, key=lambda item: abs(item["net_score"]), reverse=True),
            "calculation": {
                "formula": (
                    "magnitude × event_importance × assessment_confidence × "
                    "path_confidence × time_weight × dependency_weight"
                ),
                "rule_version": RULE_VERSION,
                "as_of": as_of,
            },
        }

    def timeline(
        self,
        target_id: str,
        *,
        start: datetime,
        end: datetime,
        granularity: str = "auto",
        horizon: str | None = None,
        scenario_set_id: str = "baseline",
    ) -> dict[str, Any] | None:
        if self.repository.get_impact_target(target_id) is None:
            return None
        if end < start:
            raise ValueError("TIMELINE_RANGE_INVALID")
        days = max(1, (end - start).days)
        step = {"day": 1, "week": 7, "month": 30}.get(granularity)
        if step is None:
            step = 1 if days <= 45 else 7 if days <= 365 else 30
            granularity = {1: "day", 7: "week", 30: "month"}[step]
        points: list[dict[str, Any]] = []
        cursor = start
        while cursor <= end and len(points) < 400:
            snapshot = self.recompute_target(
                target_id,
                as_of=cursor,
                horizon=horizon,
                scenario_set_id=scenario_set_id,
                persist=False,
            )
            if snapshot:
                points.append(
                    {
                        "point_at": cursor,
                        "positive_gross": snapshot.positive_gross,
                        "negative_gross": snapshot.negative_gross,
                        "net_score": snapshot.net_score,
                        "direction": snapshot.direction,
                        "confidence": snapshot.confidence,
                        "dominant_event_id": snapshot.dominant_event_id,
                    }
                )
            cursor += timedelta(days=step)
        return {"target_id": target_id, "granularity": granularity, "points": points}

    def _rationale_for_target(self, analysis: ImpactAnalysis, target: Any) -> str:
        for item in analysis.impacts:
            if item.get("target_code") == target.target_code or (
                item.get("target_name") == target.canonical_name
            ):
                return str(item.get("rationale", ""))
        for item in (analysis.analysis_payload or {}).get("impact_assessments", []):
            if item.get("target_code") == target.target_code or (
                item.get("target_name") == target.canonical_name
            ):
                return str(item.get("rationale", ""))
        return ""

    def _scenario_matches(self, scenario_id: str, scenario_set_id: str) -> bool:
        if scenario_set_id in {"baseline", "base", "scn_base"}:
            return scenario_id in {"baseline", "base", "scn_base"}
        return scenario_id in {scenario_set_id, f"scn_{scenario_set_id}"}

    def _target_scope(self, target_id: str, *, as_of: datetime | None = None) -> dict[str, float]:
        """Resolve valid descendants and apply configurable decay per level."""
        decay = max(0.0, min(1.0, float(os.getenv("FINSIGHT_TARGET_PROPAGATION_DECAY", "0.85"))))
        children: dict[str, list[Any]] = {}
        for target in self.repository.list_impact_targets():
            if as_of and (
                (target.valid_from and target.valid_from > as_of)
                or (target.valid_to and as_of > target.valid_to)
            ):
                continue
            if target.parent_target_id and target.hierarchy_status == "approved":
                children.setdefault(target.parent_target_id, []).append(target)
        scope = {target_id: 1.0}
        frontier = [(target_id, 1.0)]
        while frontier:
            parent, parent_weight = frontier.pop()
            for child in children.get(parent, []):
                if child.id in scope:
                    continue
                child_weight = max(0.0, min(1.0, child.propagation_weight or decay))
                weight = round(parent_weight * child_weight, 6)
                scope[child.id] = weight
                frontier.append((child.id, weight))
        return scope

    def recompute_all(self, *, as_of: datetime | None = None) -> list[TargetImpactSnapshot]:
        approved = []
        for event in self.repository.list_events():
            analysis = self.repository.get_latest_impact_analysis_for_event(event.id)
            if analysis and analysis.status == "approved":
                approved.extend(self.project_analysis(analysis))
        targets = {item.target_id for item in approved}
        return [
            snapshot
            for target_id in targets
            if (snapshot := self.recompute_target(target_id, as_of=as_of))
        ]

    def graph(self, target_id: str) -> dict[str, Any]:
        """返回多事件汇聚图，节点沿用单事件图契约，便于前端复用 React Flow。"""
        target = self.repository.get_impact_target(target_id)
        if target is None:
            return {"nodes": [], "edges": []}
        contributions = self.repository.list_impact_contributions(target_id)
        approved_ids = {
            item.id
            for item in (
                self.repository.get_latest_impact_analysis_for_event(contribution.event_id)
                for contribution in contributions
            )
            if item is not None and item.status == "approved"
        }
        contributions = [item for item in contributions if item.analysis_id in approved_ids]
        nodes = [
            {
                "node_id": f"target_{target.id}",
                "node_type": "impact",
                "label": target.canonical_name,
                "layer": 4,
            }
        ]
        edges = []
        for index, contribution in enumerate(contributions):
            event = self.repository.get_event(contribution.event_id)
            event_node = f"event_{contribution.event_id}"
            if not any(item["node_id"] == event_node for item in nodes):
                nodes.append(
                    {
                        "node_id": event_node,
                        "node_type": "event",
                        "label": event.title if event else contribution.event_id,
                        "layer": 0,
                    }
                )
            dimension_items = self.repository.list_impact_dimension_contributions(contribution.id)
            if not dimension_items:
                dimension_items = [
                    ImpactDimensionContribution(
                        id=f"legacy_{contribution.id}",
                        contribution_id=contribution.id,
                        dimension="other",
                        direction=contribution.direction,
                        magnitude=contribution.magnitude,
                        base_strength=contribution.base_strength,
                        effective_strength=contribution.effective_strength,
                        confidence=contribution.assessment_confidence,
                    )
                ]
            for dimension_item in dimension_items:
                mechanism_node = f"mechanism_{contribution.id}_{dimension_item.dimension}"
                dimension_node = f"dimension_{contribution.id}_{dimension_item.dimension}"
                nodes.extend(
                    [
                        {
                            "node_id": mechanism_node,
                            "node_type": "mechanism",
                            "label": contribution.target_role,
                            "layer": 1,
                        },
                        {
                            "node_id": dimension_node,
                            "node_type": "financial_dimension",
                            "label": dimension_item.dimension,
                            "layer": 2,
                        },
                    ]
                )
                edges.extend(
                    [
                        {
                            "edge_id": f"aggregate_{contribution.id}_{index}_mechanism",
                            "source_node_id": event_node,
                            "target_node_id": mechanism_node,
                            "mechanism": contribution.target_role,
                            "direction": dimension_item.direction,
                            "order": "first_order",
                            "inference_kind": contribution.inference_kind,
                            "confidence": contribution.relationship_confidence,
                            "evidence_refs": contribution.evidence_refs,
                        },
                        {
                            "edge_id": f"aggregate_{contribution.id}_{index}_dimension",
                            "source_node_id": mechanism_node,
                            "target_node_id": dimension_node,
                            "mechanism": "财务维度传导",
                            "direction": dimension_item.direction,
                            "order": "second_order",
                            "inference_kind": contribution.inference_kind,
                            "confidence": dimension_item.confidence,
                            "evidence_refs": dimension_item.evidence_refs,
                        },
                        {
                            "edge_id": f"aggregate_{contribution.id}_{index}_target",
                            "source_node_id": dimension_node,
                            "target_node_id": f"target_{target.id}",
                            "mechanism": "组合影响贡献",
                            "direction": dimension_item.direction,
                            "order": "third_order",
                            "horizon": contribution.horizon,
                            "inference_kind": contribution.inference_kind,
                            "confidence": dimension_item.confidence,
                            "evidence_refs": dimension_item.evidence_refs,
                        },
                    ]
                )
        return {"nodes": nodes, "edges": edges}

    def _target_for_assessment(self, assessment: dict[str, Any]):
        target_type = str(assessment.get("target_type", "industry"))
        target_code = str(
            assessment.get("target_code")
            or self._stable_code(target_type, assessment.get("target_name", "unknown"))
        )
        existing = self.repository.find_impact_target(target_type, target_code)
        if existing:
            return existing
        from app.domain import ImpactTargetDefinition

        target = ImpactTargetDefinition(
            new_id("tgt"),
            target_type,
            target_code,
            str(assessment.get("target_name", target_code)),
            aliases=[],
        )
        self.repository.save_impact_target(target)
        return target

    def _legacy_assessment(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        return {
            "assessment_id": f"ia_legacy_{index}",
            "scenario_id": "baseline",
            "target_type": item.get("target_type", "industry"),
            "target_name": item.get("target_name", "unknown"),
            "target_code": item.get("target_code"),
            "direction": item.get("direction", "uncertain"),
            "magnitude": item.get("magnitude", "uncertain"),
            "horizon": self._legacy_horizon(item.get("horizon")),
            "confidence": item.get("confidence", 0.0),
        }

    def _path_confidence(self, payload: dict[str, Any], assessment: dict[str, Any]) -> float:
        refs = assessment.get("causal_edge_refs") or []
        edges = {
            item.get("edge_id"): item
            for item in (payload.get("causal_graph", {}).get("edges") or [])
        }
        values = [float(edges[ref].get("confidence", 0.0)) for ref in refs if ref in edges]
        return sum(values) / len(values) if values else 0.7

    def _timing_date(self, assessment: dict[str, Any], key: str) -> datetime | None:
        value = (assessment.get("timing") or {}).get(key)
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _relation_weights(self, relations: list[Any]) -> dict[str, float]:
        result: dict[str, float] = {}
        for relation in relations:
            if relation.status != "approved":
                continue
            weight = relation.dependency_weight
            if relation.relation_type == "same_incident":
                weight = 0.0
            result[relation.target_event_id] = min(
                result.get(relation.target_event_id, 1.0), weight
            )
        return result

    def _stable_code(self, target_type: str, name: str) -> str:
        normalized = re.sub(r"\s+", "", str(name).lower())
        return f"name:{target_type}:{hashlib.sha1(normalized.encode()).hexdigest()[:16]}"

    def _legacy_horizon(self, value: Any) -> str:
        return {"short": "2_5d", "medium": "1_4q", "long": "1y_plus"}.get(value, "unknown")

    def _peak_days(self, horizon: str) -> int:
        return {"0_1d": 1, "2_5d": 3, "1_4w": 14, "1_4q": 90, "1y_plus": 365}.get(horizon, 30)

    def _end_days(self, horizon: str) -> int:
        return self._peak_days(horizon) * 4

    def _direction(self, net: float, positive: float, negative: float) -> str:
        if net >= 0.15:
            return "positive"
        if net <= -0.15:
            return "negative"
        if min(positive, negative) >= 0.35:
            return "mixed"
        return "uncertain"

    def _magnitude(self, value: float) -> str:
        if value >= 0.65:
            return "strong"
        if value >= 0.35:
            return "moderate"
        if value >= 0.15:
            return "weak"
        return "uncertain"

    def _change_type(self, previous: str | None, current: str) -> str | None:
        if previous is None or previous == current:
            return None
        if {previous, current} == {"positive", "negative"}:
            return "direction_reversal"
        return "direction_change"

    def _explanation(self, name: str, direction: str, positive: float, negative: float) -> str:
        label = {
            "positive": "利好",
            "negative": "利空",
            "mixed": "多空交织",
            "uncertain": "影响尚不确定",
        }[direction]
        return (
            f"{name}当前综合判断为{label}；正向影响强度 {positive:.2f}，"
            f"负向影响强度 {negative:.2f}。结果由已批准事件分析按重要度、"
            "置信度、传导路径和时间权重聚合得出。"
        )
