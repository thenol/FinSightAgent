"""OOD learning services: feature snapshots, clustering, proposals and pack evaluation."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone

from app.capabilities import (
    CapabilityPack,
    CapabilityPackManifest,
    CapabilityPackRegistry,
    default_capability_registry,
)
from app.domain import (
    CapabilityEvaluation,
    EventTypeProposal,
    OODCluster,
    OODFeatureSnapshot,
    OODObservation,
    ReprocessingJob,
)
from app.platform.ids import new_id

_RUNTIME_REGISTRY = default_capability_registry()


class OODLearningService:
    """Deterministic runtime around Agent extension points.

    Agents may replace proposal generation and evaluation explanations later; state
    transitions and metrics remain runtime-owned and reproducible.
    """

    def __init__(self, repository, registry: CapabilityPackRegistry | None = None) -> None:
        self.repository = repository
        self.registry = registry or _RUNTIME_REGISTRY

    def snapshot_features(self, observation: OODObservation) -> OODFeatureSnapshot:
        features = observation.extracted_features or {}
        snapshot = OODFeatureSnapshot(
            id=new_id("odf"),
            observation_id=observation.id,
            feature_schema_version="ood-features-v1",
            features=features,
            generated_at=datetime.now(timezone.utc),
        )
        self.repository.save_ood_feature_snapshot(snapshot)
        return snapshot

    def cluster_ready_observations(self, *, limit: int = 100) -> list[OODCluster]:
        observations = self.repository.list_ood_observations(
            status="ready_for_clustering", limit=limit
        )
        clusters: dict[str, list[OODObservation]] = {}
        for observation in observations:
            label = str(
                (observation.extracted_features or {}).get("router_event_type")
                or "unknown-financial-event"
            )
            clusters.setdefault(label, []).append(observation)
        result: list[OODCluster] = []
        for label, members in clusters.items():
            digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]
            cluster_id = f"ocl_{digest}"
            timestamps = [item.observed_at for item in members if item.observed_at]
            cluster = OODCluster(
                id=cluster_id,
                label=label,
                status="forming" if len(members) < 3 else "stable",
                member_count=len(members),
                independent_source_count=len(
                    {
                        str((item.extracted_features or {}).get("source_id", item.document_id))
                        for item in members
                    }
                ),
                cohesion_score=1.0 if len(members) == 1 else 0.8,
                separation_score=0.5,
                stability_score=min(1.0, len(members) / 10),
                first_seen_at=min(timestamps) if timestamps else None,
                last_seen_at=max(timestamps) if timestamps else None,
            )
            self.repository.save_ood_cluster(cluster)
            for item in members:
                self.repository.update_ood_observation(
                    replace(item, status="clustered", cluster_id=cluster.id)
                )
            result.append(cluster)
        return result

    def propose_type(self, cluster: OODCluster) -> EventTypeProposal:
        label = _safe_label(cluster.label)
        proposal = EventTypeProposal(
            id=new_id("etp"),
            cluster_id=cluster.id,
            proposed_label=label,
            display_name=f"候选事件：{cluster.label}",
            definition=f"由候选簇 {cluster.label} 归纳出的未知财经事件类型。",
            status="draft",
            inclusion_rules=["金融相关性不低于0.70", "与候选簇代表事件具有结构化相似性"],
            exclusion_rules=["与已激活事件类型完全一致的事件"],
            required_fields=["subject", "action", "announcement_stage"],
            optional_fields=["affected_targets", "geography", "cause_status", "time_profile"],
            mechanisms=[
                {"name": "generic_market_transmission", "path": ["event", "target", "market"]}
            ],
            confidence=min(1.0, 0.5 + cluster.stability_score / 2),
            representative_event_ids=[],
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_event_type_proposal(proposal)
        return proposal

    def build_candidate_pack(self, proposal: EventTypeProposal) -> CapabilityPack:
        pack_id = f"discovered.{proposal.proposed_label}"
        pack = CapabilityPack(
            manifest=CapabilityPackManifest(
                pack_id=pack_id,
                version="0.1.0",
                status="candidate",
                display_name=proposal.display_name,
                parent_type=proposal.parent_type or "discovered_event",
                event_types=[proposal.proposed_label],
                input_schema_ref="event-document/1.0.0",
                event_schema_ref=f"{proposal.proposed_label}/0.1.0",
                analysis_schema_ref="impact-analysis/2.1.0",
                workflow_template_ref="generic-event-research/1.1.0",
                required_capabilities=["fact_verify", "impact_analyze", "skeptic_review"],
                allowed_tools=[
                    "search_official_source",
                    "resolve_security",
                    "get_market_snapshots",
                ],
                quality_gate_ref="generic-event-quality/1.0.0",
            ),
            required_fields=tuple(proposal.required_fields),
            optional_fields=tuple(proposal.optional_fields),
            research_questions=("事件事实是什么？", "哪些对象受到影响？", "哪些条件会使判断失效？"),
            mechanism_templates=tuple(proposal.mechanisms),
            target_types=("company", "industry", "market", "asset_class"),
        )
        self.registry.register(pack)
        return pack

    def evaluate_pack(self, pack: CapabilityPack) -> CapabilityEvaluation:
        completeness = 1.0 if pack.required_fields else 0.5
        metrics = {
            "field_completeness": completeness,
            "evidence_coverage": 0.80,
            "critical_error_rate": 0.0,
            "sample_count": 0,
        }
        evaluation = CapabilityEvaluation(
            id=new_id("cev"),
            pack_id=pack.manifest.pack_id,
            pack_version=pack.manifest.version,
            status="passed" if completeness >= 0.8 else "needs_review",
            metrics=metrics,
            recommendation="promote_to_shadow" if completeness >= 0.8 else "revise_pack",
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_capability_evaluation(evaluation)
        return evaluation

    def create_reprocessing_job(
        self, target_pack_id: str, event_ids: list[str], source_pack_id: str | None = None
    ) -> ReprocessingJob:
        job = ReprocessingJob(
            id=new_id("rpg"),
            source_pack_id=source_pack_id,
            target_pack_id=target_pack_id,
            event_ids=event_ids,
            status="pending",
            total_count=len(event_ids),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.repository.save_reprocessing_job(job)
        return job


def _safe_label(value: str) -> str:
    normalized = "".join(char if char.isalnum() else "_" for char in value.lower()).strip("_")
    return normalized[:40] or "unknown_financial_event"
