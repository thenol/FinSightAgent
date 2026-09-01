import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import RLock
from typing import Any, Optional, Protocol

from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domain import (
    AgentRegistration,
    Artifact,
    AuditLog,
    AutoReviewAttempt,
    Brief,
    BriefEntry,
    BudgetLedgerEntry,
    CapabilityEvaluation,
    Claim,
    ClaimEvidenceRelation,
    ConflictRecord,
    DisclosureGroup,
    DisclosureGroupMembership,
    Document,
    DocumentBlock,
    DocumentChunk,
    DocumentRevision,
    EmbeddingRecord,
    Entity,
    EntityLink,
    Event,
    EventImpactRelation,
    EventPreliminaryAssessment,
    EventTypeProposal,
    EventTypeRegistryEntry,
    EvidenceSpan,
    FactCard,
    ForwardCatalyst,
    ForwardImpactContribution,
    ForwardImpactPoint,
    ForwardImpactWindow,
    FutureEvent,
    FutureEventRevision,
    FutureEventTargetImpact,
    ImpactAnalysis,
    ImpactContribution,
    ImpactDimensionContribution,
    ImpactGraphLayout,
    ImpactTargetDefinition,
    ImpactTargetMapping,
    IndustryClassification,
    IndustryTaxonomy,
    IngestRun,
    InstrumentIndustryMembership,
    LlmAgentBinding,
    LlmProviderConfig,
    MarketCalibrationVersion,
    MarketForecastOutcome,
    MarketForecastRun,
    MarketMasterDataImportRun,
    MatchDecision,
    MergeReviewTask,
    ModelRun,
    NodeAttempt,
    OODCluster,
    OODFeatureSnapshot,
    OODObservation,
    ParsedDocument,
    QuarantineItem,
    ReprocessingJob,
    ResearchPlan,
    ResearchTask,
    ReviewPolicy,
    ReviewTask,
    Security,
    Source,
    SourceCollectionConfig,
    TargetImpactSnapshot,
    TargetImpactSnapshotContribution,
    ToolCall,
    User,
    WatchTrigger,
    WorkflowRun,
)
from app.market.provider import MarketInstrument
from app.platform.asof import visible_as_of
from app.platform.db_models import (
    AgentRegistrationModel,
    ArtifactModel,
    AuditLogModel,
    AutoReviewAttemptModel,
    Base,
    BriefModel,
    BudgetLedgerModel,
    CapabilityEvaluationModel,
    ClaimEvidenceRelationModel,
    ClaimModel,
    ConflictModel,
    DisclosureGroupMembershipModel,
    DisclosureGroupModel,
    DocumentBlockModel,
    DocumentChunkModel,
    DocumentModel,
    DocumentRevisionModel,
    EmbeddingRecordModel,
    EntityModel,
    EventEntityModel,
    EventImpactRelationModel,
    EventModel,
    EventPreliminaryAssessmentModel,
    EventTypeProposalModel,
    EventTypeRegistryModel,
    EvidenceSpanModel,
    FactCardModel,
    ForwardCatalystModel,
    ForwardImpactContributionModel,
    ForwardImpactPointModel,
    ForwardImpactWindowModel,
    FutureEventModel,
    FutureEventRevisionModel,
    FutureEventTargetImpactModel,
    IdempotencyModel,
    ImpactAnalysisModel,
    ImpactContributionModel,
    ImpactDimensionContributionModel,
    ImpactGraphLayoutModel,
    ImpactTargetDefinitionModel,
    ImpactTargetMappingModel,
    InboxModel,
    IndustryClassificationModel,
    IndustryTaxonomyModel,
    IngestRunModel,
    InstrumentIndustryMembershipModel,
    LlmAgentBindingModel,
    LlmProviderConfigModel,
    MarketCalibrationVersionModel,
    MarketForecastOutcomeModel,
    MarketForecastRunModel,
    MarketInstrumentModel,
    MarketMasterDataImportRunModel,
    MatchDecisionModel,
    MergeReviewTaskModel,
    ModelRunModel,
    NodeAttemptModel,
    OODClusterModel,
    OODFeatureSnapshotModel,
    OODObservationModel,
    OutboxModel,
    ParsedDocumentModel,
    QuarantineItemModel,
    ReprocessingJobModel,
    ResearchPlanModel,
    ResearchTaskModel,
    ReviewPolicyModel,
    ReviewTaskModel,
    SecurityModel,
    SourceCollectionConfigModel,
    SourceModel,
    TargetImpactSnapshotContributionModel,
    TargetImpactSnapshotModel,
    ToolCallModel,
    UserModel,
    WatchTriggerModel,
    WorkflowRunModel,
)
from app.platform.ids import new_id
from app.platform.pagination import decode_cursor


class RetentionHoldError(Exception):
    """Raised when soft-delete or purge is blocked by Document.retention_hold."""

    def __init__(self, document_id: str) -> None:
        self.document_id = document_id
        super().__init__(f"RETENTION_HOLD:{document_id}")


class DocumentNotSoftDeletedError(Exception):
    """Raised when purge is requested for a document that is not soft-deleted."""

    def __init__(self, document_id: str) -> None:
        self.document_id = document_id
        super().__init__(f"DOCUMENT_NOT_SOFT_DELETED:{document_id}")


class PurgeRetentionWindowError(Exception):
    """Raised when purge is attempted before the soft-delete retention window elapses."""

    def __init__(self, document_id: str, eligible_at: datetime) -> None:
        self.document_id = document_id
        self.eligible_at = eligible_at
        super().__init__(f"PURGE_RETENTION_WINDOW:{document_id}")


def _assert_purge_retention_window(
    document_id: str,
    deleted_at: datetime,
    *,
    min_soft_delete_age_seconds: int,
    now: datetime,
) -> None:
    if min_soft_delete_age_seconds <= 0:
        return
    eligible_at = deleted_at + timedelta(seconds=min_soft_delete_age_seconds)
    if now < eligible_at:
        raise PurgeRetentionWindowError(document_id, eligible_at)


class PipelineResultReference:
    def __init__(
        self,
        document_id: str,
        event_id: str,
        fact_card_id: str,
        request_hash: str,
    ) -> None:
        self.document_id = document_id
        self.event_id = event_id
        self.fact_card_id = fact_card_id
        self.request_hash = request_hash


class ReportVersionConflict(ValueError):
    """报告版本已被另一写入者创建，调用方应重新读取最新版本。"""


@dataclass(frozen=True)
class ApiIdempotencyRecord:
    request_hash: str
    operation: str
    resource_id: str
    response: dict[str, Any]


@dataclass(frozen=True)
class OutboxMessage:
    id: str
    event_type: str
    aggregate_id: str
    payload: dict
    trace_id: str
    attempts: int
    created_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    next_attempt_at: Optional[datetime] = None
    last_error: Optional[str] = None
    dead_lettered_at: Optional[datetime] = None


class Repository(Protocol):
    def save_user(self, user: User) -> None: ...

    def get_user_by_username(self, username: str) -> Optional[User]: ...

    def get_user(self, user_id: str) -> Optional[User]: ...

    def list_users(self) -> list[User]: ...

    def update_user(self, user: User) -> None: ...

    def save_audit_log(self, log: AuditLog) -> None: ...

    def list_audit_logs(
        self, limit: Optional[int] = 100, cursor: Optional[str] = None
    ) -> list[AuditLog]: ...

    def save_review_task(self, task: ReviewTask) -> None: ...

    def get_review_task(self, task_id: str) -> Optional[ReviewTask]: ...

    def list_review_tasks(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[ReviewTask]: ...

    def update_review_task(self, task: ReviewTask) -> None: ...

    def get_review_policy(self) -> ReviewPolicy: ...

    def save_review_policy(self, policy: ReviewPolicy) -> None: ...

    def get_source_collection_config(self) -> SourceCollectionConfig: ...

    def save_source_collection_config(self, config: SourceCollectionConfig) -> None: ...

    def save_auto_review_attempt(self, attempt: AutoReviewAttempt) -> None: ...

    def list_auto_review_attempts(
        self, task_id: str, limit: int = 20
    ) -> list[AutoReviewAttempt]: ...

    def save_model_run(self, run: ModelRun) -> None: ...

    def find_model_run_by_hash(self, request_hash: str) -> Optional[ModelRun]: ...

    def list_model_runs(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[ModelRun]: ...

    def save_workflow_run(self, run: WorkflowRun) -> None: ...

    def get_workflow_run(self, workflow_id: str) -> Optional[WorkflowRun]: ...

    def list_workflow_runs(
        self,
        event_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[WorkflowRun]: ...

    def update_workflow_run(self, run: WorkflowRun) -> None: ...

    def save_tool_call(self, call: ToolCall) -> None: ...

    def list_tool_calls(self, workflow_id: str) -> list[ToolCall]: ...

    def save_budget_ledger(self, entry: BudgetLedgerEntry) -> None: ...

    def list_budget_ledger(self, workflow_id: str) -> list[BudgetLedgerEntry]: ...

    def save_node_attempt(self, attempt: NodeAttempt) -> None: ...

    def find_node_attempt(
        self, workflow_id: str, node_name: str, input_hash: str
    ) -> Optional[NodeAttempt]: ...

    def list_node_attempts(
        self, workflow_id: str, node_name: Optional[str] = None
    ) -> list[NodeAttempt]: ...

    def invalidate_node_attempts(self, workflow_id: str, node_names: list[str]) -> int: ...

    def save_source(self, source: Source) -> None: ...

    def get_source(self, source_id: str) -> Optional[Source]: ...

    def get_source_by_code(self, code: str) -> Optional[Source]: ...

    def list_sources(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[Source]: ...

    def update_source(self, source: Source) -> None: ...

    def save_ingest_run(self, run: IngestRun) -> None: ...

    def update_ingest_run(self, run: IngestRun) -> None: ...

    def get_ingest_run(self, run_id: str) -> Optional[IngestRun]: ...

    def list_ingest_runs(
        self,
        source_id: str,
        limit: Optional[int] = 20,
        cursor: Optional[str] = None,
    ) -> list[IngestRun]: ...

    def save_llm_provider(self, config: LlmProviderConfig) -> None: ...

    def get_llm_provider(self, provider_id: str) -> Optional[LlmProviderConfig]: ...

    def get_llm_provider_by_code(self, code: str) -> Optional[LlmProviderConfig]: ...

    def get_default_llm_provider(self) -> Optional[LlmProviderConfig]: ...

    def list_llm_providers(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[LlmProviderConfig]: ...

    def update_llm_provider(self, config: LlmProviderConfig) -> None: ...

    def delete_llm_provider(self, provider_id: str) -> None: ...

    def upsert_llm_agent_binding(self, binding: LlmAgentBinding) -> None: ...

    def get_llm_agent_binding(self, agent_key: str) -> Optional[LlmAgentBinding]: ...

    def list_llm_agent_bindings(self) -> list[LlmAgentBinding]: ...

    def save_quarantine_item(self, item: QuarantineItem) -> None: ...

    def list_quarantine_items(
        self,
        source_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[QuarantineItem]: ...

    def get_quarantine_item(self, item_id: str) -> Optional[QuarantineItem]: ...

    def update_quarantine_item(self, item: QuarantineItem) -> None: ...

    def list_outbox(
        self,
        dead_lettered: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[OutboxMessage]: ...

    def get_outbox(self, outbox_id: str) -> Optional[OutboxMessage]: ...

    def retry_outbox(self, outbox_id: str) -> None: ...

    def list_pending_outbox(
        self, limit: int, now: Optional[datetime] = None
    ) -> list[OutboxMessage]: ...

    def list_pending_outbox_by_event_type(
        self, event_type: str, limit: int, now: Optional[datetime] = None
    ) -> list[OutboxMessage]: ...

    def mark_outbox_published(self, message_id: str, published_at: datetime) -> None: ...

    def find_document(
        self, source_id: str, external_id: Optional[str], content_hash: str
    ) -> Optional[Document]: ...

    def save_document(self, document: Document) -> None: ...

    def update_document(self, document: Document) -> None: ...

    def find_artifact(self, sha256: str) -> Optional[Artifact]: ...

    def save_artifact(self, artifact: Artifact) -> None: ...

    def save_document_revision(self, revision: DocumentRevision) -> None: ...

    def get_latest_revision(
        self, document_id: str, as_of: Optional[datetime] = None
    ) -> Optional[DocumentRevision]: ...

    def save_parsed_document(self, parsed: ParsedDocument) -> None: ...

    def get_parsed_document_by_document(self, document_id: str) -> Optional[ParsedDocument]: ...

    def get_parsed_document_by_revision(self, revision_id: str) -> Optional[ParsedDocument]: ...

    def save_document_block(self, block: DocumentBlock) -> None: ...

    def get_document_block(self, block_id: str) -> Optional[DocumentBlock]: ...

    def get_document_blocks_for_revision(self, revision_id: str) -> list[DocumentBlock]: ...

    def save_document_chunk(self, chunk: DocumentChunk) -> None: ...

    def get_document_chunks_for_block(self, block_id: str) -> list[DocumentChunk]: ...

    def save_embedding_record(self, record: EmbeddingRecord) -> None: ...

    def get_embedding_record(self, record_id: str) -> Optional[EmbeddingRecord]: ...

    def find_embedding_record_by_chunk_and_model(
        self, chunk_id: str, model_version: str
    ) -> Optional[EmbeddingRecord]: ...

    def list_embedding_records_by_chunks(self, chunk_ids: list[str]) -> list[EmbeddingRecord]: ...

    def find_similar_document_chunks(
        self,
        query_embedding: list[float],
        model_version: str,
        top_k: int = 10,
        as_of: Optional[datetime] = None,
        chunk_types: Optional[list[str]] = None,
        source_tiers: Optional[list[str]] = None,
    ) -> list[tuple[DocumentChunk, float]]: ...

    def find_document_chunks_by_keywords(
        self,
        keywords: list[str],
        top_k: int = 10,
        as_of: Optional[datetime] = None,
        chunk_types: Optional[list[str]] = None,
        source_tiers: Optional[list[str]] = None,
    ) -> list[tuple[DocumentChunk, float]]: ...

    def list_disclosure_groups_with_embeddings(
        self, model_version: str
    ) -> list[DisclosureGroup]: ...

    def find_similar_disclosure_groups(
        self,
        query_embedding: list[float],
        model_version: str,
        top_k: int = 10,
    ) -> list[tuple[DisclosureGroup, float]]: ...

    def save_disclosure_group(self, group: DisclosureGroup) -> None: ...

    def get_disclosure_group(self, group_id: str) -> Optional[DisclosureGroup]: ...

    def find_disclosure_group_by_content_hash(
        self, canonical_content_hash: str
    ) -> Optional[DisclosureGroup]: ...

    def save_disclosure_group_membership(self, membership: DisclosureGroupMembership) -> None: ...

    def list_disclosure_group_members(self, group_id: str) -> list[DisclosureGroupMembership]: ...

    def get_disclosure_group_for_document(self, document_id: str) -> Optional[DisclosureGroup]: ...

    def save_event(self, event: Event) -> None: ...

    def update_event(self, event: Event) -> None: ...

    def save_entity(self, entity: Entity) -> None: ...

    def get_entity(self, entity_id: str) -> Optional[Entity]: ...

    def save_security(self, security: Security) -> None: ...

    def get_security_by_market_code(self, market_code: str) -> Optional[Security]: ...

    def save_event_entities(self, event_id: str, links: list[EntityLink]) -> None: ...

    def list_event_entities(self, event_id: str) -> list[EntityLink]: ...

    def save_merge_review_task(self, task: MergeReviewTask) -> None: ...

    def get_merge_review_task(self, task_id: str) -> Optional[MergeReviewTask]: ...

    def list_merge_review_tasks(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[MergeReviewTask]: ...

    def update_merge_review_task(self, task: MergeReviewTask) -> None: ...

    def save_watch_trigger(self, trigger: WatchTrigger) -> None: ...

    def get_watch_trigger(self, trigger_id: str) -> Optional[WatchTrigger]: ...

    def list_watch_triggers(
        self,
        status: Optional[str] = None,
        event_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[WatchTrigger]: ...

    def update_watch_trigger(self, trigger: WatchTrigger) -> None: ...

    def get_event_type_registry(self, type_label: str) -> Optional[EventTypeRegistryEntry]: ...

    def list_event_type_registry(
        self, status: Optional[str] = None
    ) -> list[EventTypeRegistryEntry]: ...

    def save_event_type_registry(self, entry: EventTypeRegistryEntry) -> None: ...

    def increment_event_type_registry_count(self, type_label: str) -> EventTypeRegistryEntry: ...

    def save_ood_observation(self, observation: OODObservation) -> None: ...

    def get_ood_observation(self, observation_id: str) -> Optional[OODObservation]: ...

    def list_ood_observations(
        self, status: Optional[str] = None, limit: Optional[int] = None
    ) -> list[OODObservation]: ...

    def update_ood_observation(self, observation: OODObservation) -> None: ...

    def save_ood_cluster(self, cluster: OODCluster) -> None: ...

    def get_ood_cluster(self, cluster_id: str) -> Optional[OODCluster]: ...

    def list_ood_clusters(self, status: Optional[str] = None) -> list[OODCluster]: ...

    def save_ood_feature_snapshot(self, snapshot: OODFeatureSnapshot) -> None: ...
    def get_ood_feature_snapshot(self, snapshot_id: str) -> Optional[OODFeatureSnapshot]: ...
    def save_event_type_proposal(self, proposal: EventTypeProposal) -> None: ...
    def get_event_type_proposal(self, proposal_id: str) -> Optional[EventTypeProposal]: ...
    def list_event_type_proposals(
        self, status: Optional[str] = None
    ) -> list[EventTypeProposal]: ...
    def update_event_type_proposal(self, proposal: EventTypeProposal) -> None: ...
    def save_capability_evaluation(self, evaluation: CapabilityEvaluation) -> None: ...
    def get_capability_evaluation(self, evaluation_id: str) -> Optional[CapabilityEvaluation]: ...
    def list_capability_evaluations(
        self, pack_id: Optional[str] = None
    ) -> list[CapabilityEvaluation]: ...
    def save_reprocessing_job(self, job: ReprocessingJob) -> None: ...
    def get_reprocessing_job(self, job_id: str) -> Optional[ReprocessingJob]: ...
    def list_reprocessing_jobs(self) -> list[ReprocessingJob]: ...
    def update_reprocessing_job(self, job: ReprocessingJob) -> None: ...

    def save_match_decision(self, decision: MatchDecision) -> None: ...

    def list_match_decisions(self, document_id: str) -> list[MatchDecision]: ...

    def save_evidence(self, evidence: EvidenceSpan) -> None: ...

    def save_claim(self, claim: Claim) -> None: ...

    def update_claim(self, claim: Claim) -> None: ...

    def count_claims(self) -> int: ...

    def count_claims_with_evidence(self) -> int: ...

    def find_claim_by_fingerprint(
        self, event_id: str, fingerprint: str, as_of: Optional[datetime] = None
    ) -> Optional[Claim]: ...

    def save_claim_evidence(self, relation: "ClaimEvidenceRelation") -> None: ...

    def save_conflict(self, conflict: "ConflictRecord") -> None: ...

    def get_conflict(self, conflict_id: str) -> Optional["ConflictRecord"]: ...

    def update_conflict(self, conflict: "ConflictRecord") -> None: ...

    def list_claim_evidence(self, claim_id: str) -> list["ClaimEvidenceRelation"]: ...

    def list_conflicts_for_event(
        self, event_id: str, as_of: Optional[datetime] = None
    ) -> list["ConflictRecord"]: ...

    def save_fact_card(self, fact_card: FactCard) -> None: ...

    def list_events(
        self,
        as_of: Optional[datetime] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        *,
        event_types: Optional[list[str]] = None,
        entity_ids: Optional[list[str]] = None,
        occurred_from: Optional[datetime] = None,
        occurred_to: Optional[datetime] = None,
    ) -> list[Event]: ...

    def get_document(
        self, document_id: str, *, include_deleted: bool = False
    ) -> Optional[Document]: ...

    def get_event(self, event_id: str) -> Optional[Event]: ...

    def find_event_by_document(
        self, document_id: str, as_of: Optional[datetime] = None
    ) -> Optional[Event]: ...

    def get_evidence(
        self, evidence_id: str, *, include_deleted: bool = False
    ) -> Optional[EvidenceSpan]: ...

    def set_document_retention_hold(self, document_id: str, hold: bool) -> Document: ...

    def soft_delete_document(
        self, document_id: str, *, deleted_at: Optional[datetime] = None
    ) -> Document: ...

    def purge_document(
        self,
        document_id: str,
        *,
        purged_at: Optional[datetime] = None,
        min_soft_delete_age_seconds: int = 0,
    ) -> Document: ...

    def list_documents_eligible_for_purge(
        self,
        *,
        deleted_before: datetime,
        limit: int = 100,
    ) -> list[Document]: ...

    def get_claim(self, claim_id: str) -> Optional[Claim]: ...

    def get_claims_for_event(
        self, event_id: str, as_of: Optional[datetime] = None
    ) -> list[Claim]: ...

    def get_fact_card(self, fact_card_id: str) -> Optional[FactCard]: ...

    def get_fact_card_for_event(
        self, event_id: str, as_of: Optional[datetime] = None
    ) -> Optional[FactCard]: ...

    def list_fact_cards_for_event(self, event_id: str) -> list[FactCard]: ...

    def list_fact_cards(
        self,
        event_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[FactCard]: ...

    def list_published_reports(self, start: datetime, end: datetime) -> list[FactCard]: ...

    def save_impact_analysis(self, impact_analysis: "ImpactAnalysis") -> None: ...

    def get_impact_analysis(self, impact_analysis_id: str) -> Optional["ImpactAnalysis"]: ...

    def get_latest_impact_analysis_for_event(self, event_id: str) -> Optional["ImpactAnalysis"]: ...

    def list_impact_analyses_for_event(
        self, event_id: str, limit: Optional[int] = None
    ) -> list["ImpactAnalysis"]: ...

    def update_impact_analysis(self, impact_analysis: "ImpactAnalysis") -> None: ...
    def save_preliminary_assessment(self, assessment: "EventPreliminaryAssessment") -> None: ...
    def get_preliminary_assessment(
        self, assessment_id: str
    ) -> Optional["EventPreliminaryAssessment"]: ...
    def get_latest_preliminary_assessment_for_event(
        self, event_id: str
    ) -> Optional["EventPreliminaryAssessment"]: ...
    def list_preliminary_assessments_for_event(
        self, event_id: str, limit: Optional[int] = None
    ) -> list["EventPreliminaryAssessment"]: ...
    def update_preliminary_assessment(self, assessment: "EventPreliminaryAssessment") -> None: ...

    def get_impact_graph_layout(
        self, analysis_id: str, user_id: str
    ) -> Optional[ImpactGraphLayout]: ...

    def save_impact_graph_layout(self, layout: ImpactGraphLayout) -> None: ...

    def delete_impact_graph_layout(self, analysis_id: str, user_id: str) -> None: ...

    def save_impact_target(self, target: ImpactTargetDefinition) -> None: ...
    def get_impact_target(self, target_id: str) -> Optional[ImpactTargetDefinition]: ...
    def find_impact_target(
        self, target_type: str, target_code: str, taxonomy_version: str = "default-v1"
    ) -> Optional[ImpactTargetDefinition]: ...
    def list_impact_targets(
        self, target_type: Optional[str] = None
    ) -> list[ImpactTargetDefinition]: ...
    def save_market_instrument(self, value: MarketInstrument) -> None: ...
    def get_market_instrument(self, instrument_id: str) -> Optional[MarketInstrument]: ...
    def list_market_instruments(self, active: Optional[bool] = None) -> list[MarketInstrument]: ...
    def save_industry_taxonomy(self, value: IndustryTaxonomy) -> None: ...
    def list_industry_taxonomies(self, status: Optional[str] = None) -> list[IndustryTaxonomy]: ...
    def save_industry_classification(self, value: IndustryClassification) -> None: ...
    def list_industry_classifications(
        self, taxonomy_id: Optional[str] = None
    ) -> list[IndustryClassification]: ...
    def save_instrument_industry_membership(self, value: InstrumentIndustryMembership) -> None: ...
    def list_instrument_industry_memberships(
        self, instrument_id: Optional[str] = None, status: Optional[str] = None
    ) -> list[InstrumentIndustryMembership]: ...
    def save_impact_target_mapping(self, value: ImpactTargetMapping) -> None: ...
    def get_impact_target_mapping(self, mapping_id: str) -> Optional[ImpactTargetMapping]: ...
    def update_impact_target_mapping(self, value: ImpactTargetMapping) -> None: ...
    def list_impact_target_mappings(
        self, target_id: Optional[str] = None, status: Optional[str] = None
    ) -> list[ImpactTargetMapping]: ...
    def save_market_master_data_import_run(self, value: MarketMasterDataImportRun) -> None: ...
    def get_market_master_data_import_run(
        self, run_id: str
    ) -> Optional[MarketMasterDataImportRun]: ...
    def find_market_master_data_import_run_by_hash(
        self, source_hash: str
    ) -> Optional[MarketMasterDataImportRun]: ...
    def update_market_master_data_import_run(self, value: MarketMasterDataImportRun) -> None: ...
    def list_market_master_data_import_runs(self) -> list[MarketMasterDataImportRun]: ...
    def save_event_impact_relation(self, relation: EventImpactRelation) -> None: ...
    def list_event_impact_relations(
        self, event_id: Optional[str] = None
    ) -> list[EventImpactRelation]: ...
    def save_impact_contribution(self, contribution: ImpactContribution) -> None: ...
    def list_impact_contributions(
        self, target_id: Optional[str] = None
    ) -> list[ImpactContribution]: ...

    def save_impact_dimension_contribution(self, item: ImpactDimensionContribution) -> None: ...
    def list_impact_dimension_contributions(
        self, contribution_id: Optional[str] = None
    ) -> list[ImpactDimensionContribution]: ...
    def save_target_impact_snapshot(
        self, snapshot: TargetImpactSnapshot, contributions: list[TargetImpactSnapshotContribution]
    ) -> None: ...
    def get_latest_target_impact_snapshot(
        self,
        target_id: str,
        horizon: Optional[str] = None,
        scenario_set_id: str = "baseline",
        as_of: Optional[datetime] = None,
    ) -> Optional[TargetImpactSnapshot]: ...
    def list_target_impact_snapshot_contributions(
        self, snapshot_id: str
    ) -> list[TargetImpactSnapshotContribution]: ...
    def save_market_forecast_run(self, value: MarketForecastRun) -> None: ...
    def get_market_forecast_run(self, forecast_id: str) -> Optional[MarketForecastRun]: ...
    def find_market_forecast_run_by_source_hash(
        self, source_hash: str
    ) -> Optional[MarketForecastRun]: ...
    def list_market_forecast_runs(
        self,
        instrument_id: Optional[str] = None,
        horizon: Optional[int] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 500,
    ) -> list[MarketForecastRun]: ...
    def save_market_forecast_outcome(self, value: MarketForecastOutcome) -> None: ...
    def get_market_forecast_outcome(self, forecast_id: str) -> Optional[MarketForecastOutcome]: ...
    def list_market_forecast_outcomes(
        self, forecast_ids: Optional[list[str]] = None
    ) -> list[MarketForecastOutcome]: ...
    def save_market_calibration_version(self, value: MarketCalibrationVersion) -> None: ...
    def get_market_calibration_version(
        self, calibration_id: str
    ) -> Optional[MarketCalibrationVersion]: ...
    def update_market_calibration_version(self, value: MarketCalibrationVersion) -> None: ...
    def list_market_calibration_versions(
        self,
        model_key: Optional[str] = None,
        market: Optional[str] = None,
        horizon: Optional[int] = None,
        status: Optional[str] = None,
    ) -> list[MarketCalibrationVersion]: ...

    def save_forward_impact_window(self, value: ForwardImpactWindow) -> None: ...
    def get_forward_impact_window(self, window_id: str) -> Optional[ForwardImpactWindow]: ...
    def save_forward_catalyst(self, value: ForwardCatalyst) -> None: ...
    def get_forward_catalyst(self, catalyst_id: str) -> Optional[ForwardCatalyst]: ...
    def list_forward_catalysts(self, target_id: Optional[str] = None) -> list[ForwardCatalyst]: ...
    def save_forward_contribution(self, value: ForwardImpactContribution) -> None: ...
    def list_forward_contributions(self, window_id: str) -> list[ForwardImpactContribution]: ...
    def save_forward_points(self, values: list[ForwardImpactPoint]) -> None: ...
    def list_forward_points(
        self, window_id: str, scenario_id: str = "baseline"
    ) -> list[ForwardImpactPoint]: ...
    def save_future_event(self, value: FutureEvent) -> None: ...
    def get_future_event(self, event_id: str) -> Optional[FutureEvent]: ...
    def list_future_events(self) -> list[FutureEvent]: ...
    def save_future_event_revision(self, value: FutureEventRevision) -> None: ...
    def get_future_event_revision(self, revision_id: str) -> Optional[FutureEventRevision]: ...
    def list_future_event_revisions(self, event_id: str) -> list[FutureEventRevision]: ...
    def save_future_event_target_impact(self, value: FutureEventTargetImpact) -> None: ...
    def list_future_event_target_impacts(
        self, event_id: Optional[str] = None, target_id: Optional[str] = None
    ) -> list[FutureEventTargetImpact]: ...

    # Agent Runtime (DD-80)
    def save_agent_registration(self, registration: "AgentRegistration") -> None: ...

    def get_agent_registration(self, agent_key: str) -> Optional["AgentRegistration"]: ...

    def list_agent_registrations(self) -> list["AgentRegistration"]: ...

    def save_research_plan(self, plan: "ResearchPlan") -> None: ...

    def get_research_plan(self, plan_id: str) -> Optional["ResearchPlan"]: ...

    def get_research_plan_by_workflow(self, workflow_id: str) -> Optional["ResearchPlan"]: ...

    def list_research_plans(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list["ResearchPlan"]: ...

    def update_research_plan(self, plan: "ResearchPlan") -> None: ...

    def save_research_task(self, task: "ResearchTask") -> None: ...

    def get_research_task(self, task_id: str) -> Optional["ResearchTask"]: ...

    def list_research_tasks(self, plan_id: str) -> list["ResearchTask"]: ...

    def update_research_task(self, task: "ResearchTask") -> None: ...

    def save_brief(self, brief: Brief) -> None: ...

    def get_brief_by_date(self, brief_date: str) -> Optional[Brief]: ...

    def get_idempotent(self, key: str) -> Optional[PipelineResultReference]: ...

    def save_idempotent(self, key: str, value: PipelineResultReference) -> None: ...

    def get_api_idempotent(self, key: str) -> Optional[ApiIdempotencyRecord]: ...

    def save_api_idempotent(self, key: str, value: ApiIdempotencyRecord) -> None: ...

    def add_outbox(self, event_type: str, aggregate_id: str, payload: dict) -> None: ...

    def is_inbox_processed(self, consumer: str, message_id: str) -> bool: ...

    def save_inbox_processed(
        self, consumer: str, message_id: str, result: Optional[dict] = None
    ) -> None: ...


class RepositoryProvider(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[Repository]: ...

    def list_events(self, as_of: Optional[datetime] = None) -> list[Event]: ...

    def get_event(self, event_id: str) -> Optional[Event]: ...

    def get_claims_for_event(
        self, event_id: str, as_of: Optional[datetime] = None
    ) -> list[Claim]: ...

    def get_fact_card(self, fact_card_id: str) -> Optional[FactCard]: ...

    def get_fact_card_for_event(
        self, event_id: str, as_of: Optional[datetime] = None
    ) -> Optional[FactCard]: ...

    def list_fact_cards(
        self,
        event_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[FactCard]: ...

    def get_api_idempotent(self, key: str) -> Optional[ApiIdempotencyRecord]: ...

    def save_api_idempotent(self, key: str, value: ApiIdempotencyRecord) -> None: ...

    def list_pending_outbox(
        self, limit: int, now: Optional[datetime] = None
    ) -> list[OutboxMessage]: ...

    def mark_outbox_published(self, message_id: str, published_at: datetime) -> None: ...

    def mark_outbox_failed(
        self, message_id: str, error: str, next_attempt_at: datetime
    ) -> None: ...

    def mark_outbox_dead_lettered(
        self, message_id: str, error: str, dead_lettered_at: datetime
    ) -> None: ...


class InMemoryRepository:
    """Development adapter for the first vertical slice; not production storage.

    When ``llm_config_path`` is set, LLM provider configs and agent bindings are
    persisted to disk so admin settings survive process reloads in memory mode.
    """

    def __init__(self, *, llm_config_path: Optional[str] = None) -> None:
        self._lock = RLock()
        self.documents: dict[str, Document] = {}
        self.sources: dict[str, Source] = {}
        self.ingest_runs: dict[str, IngestRun] = {}
        self.llm_providers: dict[str, LlmProviderConfig] = {}
        self.llm_agent_bindings: dict[str, LlmAgentBinding] = {}
        self.quarantine: dict[str, QuarantineItem] = {}
        self.users: dict[str, User] = {}
        self.audit_logs: list[AuditLog] = []
        self.review_tasks: dict[str, ReviewTask] = {}
        self.review_policy = ReviewPolicy()
        self.source_collection_config = SourceCollectionConfig()
        self.auto_review_attempts: list[AutoReviewAttempt] = []
        self.model_runs: dict[str, ModelRun] = {}
        self.workflow_runs: dict[str, WorkflowRun] = {}
        self.tool_calls: list[ToolCall] = []
        self.budget_ledger: list[BudgetLedgerEntry] = []
        self.node_attempts: list[NodeAttempt] = []
        self.artifacts: dict[str, Artifact] = {}
        self.revisions: dict[str, DocumentRevision] = {}
        self.events: dict[str, Event] = {}
        self.entities: dict[str, Entity] = {}
        self.securities: dict[str, Security] = {}
        self.event_entities: dict[str, list[EntityLink]] = {}
        self.merge_review_tasks: list[MergeReviewTask] = []
        self.watch_triggers: dict[str, WatchTrigger] = {}
        self.event_type_registry: dict[str, EventTypeRegistryEntry] = {}
        self.ood_observations: dict[str, OODObservation] = {}
        self.ood_clusters: dict[str, OODCluster] = {}
        self.ood_feature_snapshots: dict[str, OODFeatureSnapshot] = {}
        self.event_type_proposals: dict[str, EventTypeProposal] = {}
        self.capability_evaluations: dict[str, CapabilityEvaluation] = {}
        self.reprocessing_jobs: dict[str, ReprocessingJob] = {}
        self.match_decisions: list[MatchDecision] = []
        self.evidence: dict[str, EvidenceSpan] = {}
        self.claims: dict[str, Claim] = {}
        self.claim_evidence: dict[str, list[ClaimEvidenceRelation]] = {}
        self.conflicts: list[ConflictRecord] = []
        self.fact_cards: dict[str, FactCard] = {}
        self.impact_analyses: dict[str, ImpactAnalysis] = {}
        self.preliminary_assessments: dict[str, EventPreliminaryAssessment] = {}
        self.impact_graph_layouts: dict[tuple[str, str], ImpactGraphLayout] = {}
        self.impact_targets: dict[str, ImpactTargetDefinition] = {}
        self.market_instruments: dict[str, MarketInstrument] = {}
        self.industry_taxonomies: dict[str, IndustryTaxonomy] = {}
        self.industry_classifications: dict[str, IndustryClassification] = {}
        self.instrument_industry_memberships: dict[str, InstrumentIndustryMembership] = {}
        self.impact_target_mappings: dict[str, ImpactTargetMapping] = {}
        self.market_master_data_import_runs: dict[str, MarketMasterDataImportRun] = {}
        self.event_impact_relations: dict[str, EventImpactRelation] = {}
        self.impact_contributions: dict[str, ImpactContribution] = {}
        self.impact_dimension_contributions: dict[str, ImpactDimensionContribution] = {}
        self.target_impact_snapshots: dict[str, TargetImpactSnapshot] = {}
        self.target_impact_snapshot_contributions: list[TargetImpactSnapshotContribution] = []
        self.market_forecast_runs: dict[str, MarketForecastRun] = {}
        self.market_forecast_outcomes: dict[str, MarketForecastOutcome] = {}
        self.market_calibration_versions: dict[str, MarketCalibrationVersion] = {}
        self.forward_impact_windows: dict[str, ForwardImpactWindow] = {}
        self.forward_catalysts: dict[str, ForwardCatalyst] = {}
        self.forward_contributions: dict[str, ForwardImpactContribution] = {}
        self.forward_points: dict[str, ForwardImpactPoint] = {}
        self.future_events: dict[str, FutureEvent] = {}
        self.future_event_revisions: dict[str, FutureEventRevision] = {}
        self.future_event_target_impacts: dict[str, FutureEventTargetImpact] = {}
        self.briefs: dict[str, Brief] = {}
        self.parsed_documents: dict[str, ParsedDocument] = {}
        self.document_blocks: dict[str, DocumentBlock] = {}
        self.document_chunks: dict[str, DocumentChunk] = {}
        self.embedding_records: dict[str, EmbeddingRecord] = {}
        self.disclosure_groups: dict[str, DisclosureGroup] = {}
        self.disclosure_group_memberships: list[DisclosureGroupMembership] = []
        self.agent_registrations: dict[str, AgentRegistration] = {}
        self.research_plans: dict[str, ResearchPlan] = {}
        self.research_tasks: dict[str, ResearchTask] = {}
        self.outbox: list[dict] = []
        self.inbox: set[tuple[str, str]] = set()
        self._document_keys: dict[tuple[str, str], str] = {}
        self._content_hashes: dict[tuple[str, str], str] = {}
        self._idempotency: dict[str, PipelineResultReference] = {}
        self._api_idempotency: dict[str, ApiIdempotencyRecord] = {}
        self._llm_config_path = llm_config_path
        if self._llm_config_path:
            from pathlib import Path

            from app.model_gateway.store import load_llm_store

            providers, bindings = load_llm_store(Path(self._llm_config_path))
            self.llm_providers = providers
            self.llm_agent_bindings = bindings

    def _persist_llm_config(self) -> None:
        if not self._llm_config_path:
            return
        from pathlib import Path

        from app.model_gateway.store import save_llm_store

        save_llm_store(Path(self._llm_config_path), self.llm_providers, self.llm_agent_bindings)

    @contextmanager
    def transaction(self) -> Iterator["InMemoryRepository"]:
        with self._lock:
            yield self

    def find_document(
        self,
        source_id: str,
        external_id: Optional[str],
        content_hash: str,
    ) -> Optional[Document]:
        if external_id and (source_id, external_id) in self._document_keys:
            return self.documents[self._document_keys[(source_id, external_id)]]
        document_id = self._content_hashes.get((source_id, content_hash))
        return self.documents.get(document_id) if document_id else None

    def save_source(self, source: Source) -> None:
        self.sources[source.id] = source

    def save_llm_provider(self, config: LlmProviderConfig) -> None:
        self.llm_providers[config.id] = config
        self._persist_llm_config()

    def get_llm_provider(self, provider_id: str) -> Optional[LlmProviderConfig]:
        return self.llm_providers.get(provider_id)

    def get_llm_provider_by_code(self, code: str) -> Optional[LlmProviderConfig]:
        return next((item for item in self.llm_providers.values() if item.code == code), None)

    def get_default_llm_provider(self) -> Optional[LlmProviderConfig]:
        return next(
            (
                item
                for item in self.llm_providers.values()
                if item.is_default and item.status == "active"
            ),
            None,
        )

    def list_llm_providers(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[LlmProviderConfig]:
        return _paginate(
            list(self.llm_providers.values()),
            cursor,
            limit,
            lambda value: value.created_at,
        )

    def update_llm_provider(self, config: LlmProviderConfig) -> None:
        if config.id not in self.llm_providers:
            raise KeyError(f"LLM provider not found: {config.id}")
        self.llm_providers[config.id] = config
        self._persist_llm_config()

    def delete_llm_provider(self, provider_id: str) -> None:
        self.llm_providers.pop(provider_id, None)
        for key, binding in list(self.llm_agent_bindings.items()):
            if binding.provider_id == provider_id:
                self.llm_agent_bindings[key] = LlmAgentBinding(
                    agent_key=binding.agent_key,
                    provider_id=None,
                    model_override=binding.model_override,
                    updated_at=binding.updated_at,
                )
        self._persist_llm_config()

    def upsert_llm_agent_binding(self, binding: LlmAgentBinding) -> None:
        self.llm_agent_bindings[binding.agent_key] = binding
        self._persist_llm_config()

    def get_llm_agent_binding(self, agent_key: str) -> Optional[LlmAgentBinding]:
        return self.llm_agent_bindings.get(agent_key)

    def list_llm_agent_bindings(self) -> list[LlmAgentBinding]:
        return sorted(self.llm_agent_bindings.values(), key=lambda item: item.agent_key)

    def save_user(self, user: User) -> None:
        self.users[user.id] = user

    def get_user_by_username(self, username: str) -> Optional[User]:
        return next((user for user in self.users.values() if user.username == username), None)

    def get_user(self, user_id: str) -> Optional[User]:
        return self.users.get(user_id)

    def list_users(self) -> list[User]:
        return sorted(
            self.users.values(),
            key=lambda user: (user.created_at or _MIN_TIMESTAMP, user.id),
        )

    def update_user(self, user: User) -> None:
        if user.id not in self.users:
            raise KeyError(f"User not found: {user.id}")
        self.users[user.id] = user

    def save_audit_log(self, log: AuditLog) -> None:
        self.audit_logs.append(log)

    def list_audit_logs(
        self, limit: Optional[int] = 100, cursor: Optional[str] = None
    ) -> list[AuditLog]:
        return _paginate(list(self.audit_logs), cursor, limit, lambda value: value.created_at)

    def save_review_task(self, task: ReviewTask) -> None:
        self.review_tasks[task.id] = task

    def get_review_task(self, task_id: str) -> Optional[ReviewTask]:
        return self.review_tasks.get(task_id)

    def list_review_tasks(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[ReviewTask]:
        values: list[ReviewTask] = list(self.review_tasks.values())
        if status:
            values = [task for task in values if task.status == status]
        return _paginate(values, cursor, limit, lambda value: value.created_at)

    def update_review_task(self, task: ReviewTask) -> None:
        if task.id not in self.review_tasks:
            raise KeyError(f"Review task not found: {task.id}")
        self.review_tasks[task.id] = task

    def get_review_policy(self) -> ReviewPolicy:
        return self.review_policy

    def save_review_policy(self, policy: ReviewPolicy) -> None:
        self.review_policy = policy

    def get_source_collection_config(self) -> SourceCollectionConfig:
        return self.source_collection_config

    def save_source_collection_config(self, config: SourceCollectionConfig) -> None:
        self.source_collection_config = config

    def save_auto_review_attempt(self, attempt: AutoReviewAttempt) -> None:
        self.auto_review_attempts.append(attempt)

    def list_auto_review_attempts(self, task_id: str, limit: int = 20) -> list[AutoReviewAttempt]:
        values = [item for item in self.auto_review_attempts if item.task_id == task_id]
        return sorted(values, key=lambda item: item.created_at, reverse=True)[:limit]

    def save_model_run(self, run: ModelRun) -> None:
        self.model_runs[run.id] = run

    def find_model_run_by_hash(self, request_hash: str) -> Optional[ModelRun]:
        return next(
            (run for run in self.model_runs.values() if run.request_hash == request_hash), None
        )

    def list_model_runs(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[ModelRun]:
        return _paginate(
            list(self.model_runs.values()), cursor, limit, lambda value: value.created_at
        )

    def save_workflow_run(self, run: WorkflowRun) -> None:
        self.workflow_runs[run.id] = run

    def get_workflow_run(self, workflow_id: str) -> Optional[WorkflowRun]:
        return self.workflow_runs.get(workflow_id)

    def update_workflow_run(self, run: WorkflowRun) -> None:
        self.workflow_runs[run.id] = run

    def list_workflow_runs(
        self,
        event_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[WorkflowRun]:
        values: list[WorkflowRun] = list(self.workflow_runs.values())
        if event_id:
            values = [run for run in values if run.event_id == event_id]
        if status:
            values = [run for run in values if run.status == status]
        return _paginate(values, cursor, limit, lambda value: value.created_at)

    def save_tool_call(self, call: ToolCall) -> None:
        self.tool_calls.append(call)

    def list_tool_calls(self, workflow_id: str) -> list[ToolCall]:
        return [call for call in self.tool_calls if call.workflow_id == workflow_id]

    def save_budget_ledger(self, entry: BudgetLedgerEntry) -> None:
        self.budget_ledger.append(entry)

    def list_budget_ledger(self, workflow_id: str) -> list[BudgetLedgerEntry]:
        return [entry for entry in self.budget_ledger if entry.workflow_id == workflow_id]

    def save_node_attempt(self, attempt: NodeAttempt) -> None:
        for index, existing in enumerate(self.node_attempts):
            if existing.id == attempt.id:
                self.node_attempts[index] = attempt
                return
        self.node_attempts.append(attempt)

    def find_node_attempt(
        self, workflow_id: str, node_name: str, input_hash: str
    ) -> Optional[NodeAttempt]:
        return next(
            (
                attempt
                for attempt in self.node_attempts
                if attempt.workflow_id == workflow_id
                and attempt.node_name == node_name
                and attempt.input_hash == input_hash
                and attempt.status == "succeeded"
            ),
            None,
        )

    def list_node_attempts(
        self, workflow_id: str, node_name: Optional[str] = None
    ) -> list[NodeAttempt]:
        return [
            attempt
            for attempt in self.node_attempts
            if attempt.workflow_id == workflow_id
            and (node_name is None or attempt.node_name == node_name)
        ]

    def invalidate_node_attempts(self, workflow_id: str, node_names: list[str]) -> int:
        if not node_names:
            return 0
        names = set(node_names)
        count = 0
        updated: list[NodeAttempt] = []
        for attempt in self.node_attempts:
            if (
                attempt.workflow_id == workflow_id
                and attempt.node_name in names
                and attempt.status == "succeeded"
            ):
                updated.append(replace(attempt, status="invalidated"))
                count += 1
            else:
                updated.append(attempt)
        self.node_attempts = updated
        return count

    def get_source(self, source_id: str) -> Optional[Source]:
        return self.sources.get(source_id)

    def get_source_by_code(self, code: str) -> Optional[Source]:
        return next((source for source in self.sources.values() if source.code == code), None)

    def list_sources(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[Source]:
        # Source 无 created_at；用 id 降序稳定分页（timestamp 哨兵）。
        return _paginate(list(self.sources.values()), cursor, limit, lambda _: None)

    def update_source(self, source: Source) -> None:
        self.sources[source.id] = source

    def save_ingest_run(self, run: IngestRun) -> None:
        self.ingest_runs[run.id] = run

    def update_ingest_run(self, run: IngestRun) -> None:
        if run.id not in self.ingest_runs:
            raise KeyError(f"IngestRun not found: {run.id}")
        self.ingest_runs[run.id] = run

    def get_ingest_run(self, run_id: str) -> Optional[IngestRun]:
        return self.ingest_runs.get(run_id)

    def list_ingest_runs(
        self,
        source_id: str,
        limit: Optional[int] = 20,
        cursor: Optional[str] = None,
    ) -> list[IngestRun]:
        values = [run for run in self.ingest_runs.values() if run.source_id == source_id]
        return _paginate(values, cursor, limit, lambda value: value.started_at)

    def save_quarantine_item(self, item: QuarantineItem) -> None:
        self.quarantine[item.id] = item

    def list_quarantine_items(
        self,
        source_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[QuarantineItem]:
        values: list[QuarantineItem] = list(self.quarantine.values())
        if source_id:
            values = [item for item in values if item.source_id == source_id]
        if status:
            values = [item for item in values if item.status == status]
        return _paginate(values, cursor, limit, lambda value: value.created_at)

    def get_quarantine_item(self, item_id: str) -> Optional[QuarantineItem]:
        return self.quarantine.get(item_id)

    def update_quarantine_item(self, item: QuarantineItem) -> None:
        if item.id not in self.quarantine:
            raise KeyError(f"Quarantine item not found: {item.id}")
        self.quarantine[item.id] = item

    def save_document(self, document: Document) -> None:
        self.documents[document.id] = document
        self._content_hashes[(document.source_id, document.content_hash)] = document.id
        if document.external_id:
            self._document_keys[(document.source_id, document.external_id)] = document.id

    def update_document(self, document: Document) -> None:
        previous = self.documents[document.id]
        self._content_hashes.pop((previous.source_id, previous.content_hash), None)
        self.documents[document.id] = document
        self._content_hashes[(document.source_id, document.content_hash)] = document.id

    def find_artifact(self, sha256: str) -> Optional[Artifact]:
        return next((value for value in self.artifacts.values() if value.sha256 == sha256), None)

    def save_artifact(self, artifact: Artifact) -> None:
        self.artifacts[artifact.id] = artifact

    def save_document_revision(self, revision: DocumentRevision) -> None:
        self.revisions[revision.id] = revision

    def get_latest_revision(
        self, document_id: str, as_of: Optional[datetime] = None
    ) -> Optional[DocumentRevision]:
        values = [
            value
            for value in self.revisions.values()
            if value.document_id == document_id and visible_as_of(value, as_of)
        ]
        return max(values, key=lambda value: value.revision_no) if values else None

    def save_parsed_document(self, parsed: ParsedDocument) -> None:
        self.parsed_documents[parsed.id] = parsed

    def get_parsed_document_by_document(self, document_id: str) -> Optional[ParsedDocument]:
        values = [
            value for value in self.parsed_documents.values() if value.document_id == document_id
        ]
        return max(values, key=lambda value: value.created_at or _MIN_TIMESTAMP) if values else None

    def get_parsed_document_by_revision(self, revision_id: str) -> Optional[ParsedDocument]:
        values = [
            value for value in self.parsed_documents.values() if value.revision_id == revision_id
        ]
        return max(values, key=lambda value: value.created_at or _MIN_TIMESTAMP) if values else None

    def save_document_block(self, block: DocumentBlock) -> None:
        self.document_blocks[block.id] = block

    def get_document_block(self, block_id: str) -> Optional[DocumentBlock]:
        return self.document_blocks.get(block_id)

    def get_document_blocks_for_revision(self, revision_id: str) -> list[DocumentBlock]:
        return sorted(
            (value for value in self.document_blocks.values() if value.revision_id == revision_id),
            key=lambda value: value.order_index,
        )

    def save_document_chunk(self, chunk: DocumentChunk) -> None:
        self.document_chunks[chunk.id] = chunk

    def get_document_chunks_for_block(self, block_id: str) -> list[DocumentChunk]:
        return sorted(
            (value for value in self.document_chunks.values() if value.block_id == block_id),
            key=lambda value: value.char_start,
        )

    def save_embedding_record(self, record: EmbeddingRecord) -> None:
        self.embedding_records[record.id] = record

    def get_embedding_record(self, record_id: str) -> Optional[EmbeddingRecord]:
        return self.embedding_records.get(record_id)

    def find_embedding_record_by_chunk_and_model(
        self, chunk_id: str, model_version: str
    ) -> Optional[EmbeddingRecord]:
        return next(
            (
                value
                for value in self.embedding_records.values()
                if value.chunk_id == chunk_id and value.embedding_model_version == model_version
            ),
            None,
        )

    def list_embedding_records_by_chunks(self, chunk_ids: list[str]) -> list[EmbeddingRecord]:
        ids = set(chunk_ids)
        return [value for value in self.embedding_records.values() if value.chunk_id in ids]

    def find_similar_document_chunks(
        self,
        query_embedding: list[float],
        model_version: str,
        top_k: int = 10,
        as_of: Optional[datetime] = None,
        chunk_types: Optional[list[str]] = None,
        source_tiers: Optional[list[str]] = None,
    ) -> list[tuple[DocumentChunk, float]]:
        from app.document_intelligence.embeddings import cosine_similarity

        chunk_type_set = set(chunk_types) if chunk_types else None
        source_tier_set = set(source_tiers) if source_tiers else None
        scored: list[tuple[DocumentChunk, float]] = []

        for record in self.embedding_records.values():
            if record.embedding_model_version != model_version:
                continue
            if record.status != "completed":
                continue
            chunk = self.document_chunks.get(record.chunk_id)
            if chunk is None:
                continue
            if as_of is not None and chunk.as_of is not None and chunk.as_of > as_of:
                continue
            if chunk_type_set is not None and chunk.chunk_type not in chunk_type_set:
                continue
            block = self.document_blocks.get(chunk.block_id)
            if block is None:
                continue
            parsed = self.parsed_documents.get(block.parsed_document_id)
            if parsed is None:
                continue
            document = self.documents.get(parsed.document_id)
            if document is None:
                continue
            if source_tier_set is not None and document.source_tier not in source_tier_set:
                continue
            score = cosine_similarity(query_embedding, record.embedding)
            scored.append((chunk, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def find_document_chunks_by_keywords(
        self,
        keywords: list[str],
        top_k: int = 10,
        as_of: Optional[datetime] = None,
        chunk_types: Optional[list[str]] = None,
        source_tiers: Optional[list[str]] = None,
    ) -> list[tuple[DocumentChunk, float]]:
        from app.retrieval.lexical import score_chunk_text

        if not keywords:
            return []

        chunk_type_set = set(chunk_types) if chunk_types else None
        source_tier_set = set(source_tiers) if source_tiers else None
        scored: list[tuple[DocumentChunk, float]] = []

        for chunk in self.document_chunks.values():
            if as_of is not None and chunk.as_of is not None and chunk.as_of > as_of:
                continue
            if chunk_type_set is not None and chunk.chunk_type not in chunk_type_set:
                continue
            block = self.document_blocks.get(chunk.block_id)
            if block is None:
                continue
            parsed = self.parsed_documents.get(block.parsed_document_id)
            if parsed is None:
                continue
            document = self.documents.get(parsed.document_id)
            if document is None:
                continue
            if source_tier_set is not None and document.source_tier not in source_tier_set:
                continue
            score = score_chunk_text(chunk.text, keywords)
            if score > 0:
                scored.append((chunk, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def list_disclosure_groups_with_embeddings(self, model_version: str) -> list[DisclosureGroup]:
        return [
            value
            for value in self.disclosure_groups.values()
            if value.embedding_model_version == model_version
            and value.representative_embedding is not None
        ]

    def find_similar_disclosure_groups(
        self,
        query_embedding: list[float],
        model_version: str,
        top_k: int = 10,
    ) -> list[tuple[DisclosureGroup, float]]:
        from app.document_intelligence.embeddings import cosine_similarity

        scored: list[tuple[DisclosureGroup, float]] = []
        for group in self.disclosure_groups.values():
            if group.embedding_model_version != model_version:
                continue
            if group.representative_embedding is None:
                continue
            score = cosine_similarity(query_embedding, group.representative_embedding)
            scored.append((group, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def save_disclosure_group(self, group: DisclosureGroup) -> None:
        self.disclosure_groups[group.id] = group

    def get_disclosure_group(self, group_id: str) -> Optional[DisclosureGroup]:
        return self.disclosure_groups.get(group_id)

    def find_disclosure_group_by_content_hash(
        self, canonical_content_hash: str
    ) -> Optional[DisclosureGroup]:
        return next(
            (
                value
                for value in self.disclosure_groups.values()
                if value.canonical_content_hash == canonical_content_hash
            ),
            None,
        )

    def save_disclosure_group_membership(self, membership: DisclosureGroupMembership) -> None:
        self.disclosure_group_memberships.append(membership)

    def list_disclosure_group_members(self, group_id: str) -> list[DisclosureGroupMembership]:
        return [
            value
            for value in self.disclosure_group_memberships
            if value.disclosure_group_id == group_id
        ]

    def get_disclosure_group_for_document(self, document_id: str) -> Optional[DisclosureGroup]:
        memberships = [
            value for value in self.disclosure_group_memberships if value.document_id == document_id
        ]
        if not memberships:
            return None
        latest = max(memberships, key=lambda value: value.joined_at or _MIN_TIMESTAMP)
        return self.disclosure_groups.get(latest.disclosure_group_id)

    def save_event(self, event: Event) -> None:
        self.events[event.id] = event

    def update_event(self, event: Event) -> None:
        self.events[event.id] = event

    def save_entity(self, entity: Entity) -> None:
        self.entities[entity.id] = entity

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return self.entities.get(entity_id)

    def save_security(self, security: Security) -> None:
        self.securities[security.id] = security

    def get_security_by_market_code(self, market_code: str) -> Optional[Security]:
        return next(
            (
                security
                for security in self.securities.values()
                if security.market_code == market_code
            ),
            None,
        )

    def list_fact_cards_for_event(self, event_id: str) -> list[FactCard]:
        return sorted(
            (card for card in self.fact_cards.values() if card.event_id == event_id),
            key=lambda card: card.version,
            reverse=True,
        )

    def list_fact_cards(
        self,
        event_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[FactCard]:
        values = (
            card
            for card in self.fact_cards.values()
            if (event_id is None or card.event_id == event_id)
            and (status is None or card.status == status)
        )
        ordered = sorted(
            values,
            key=lambda card: (card.as_of, card.id),
            reverse=True,
        )
        if cursor:
            cursor_at, cursor_id = decode_cursor(cursor)
            ordered = [card for card in ordered if (card.as_of, card.id) < (cursor_at, cursor_id)]
        return ordered if limit is None else ordered[:limit]

    def list_published_reports(self, start: datetime, end: datetime) -> list[FactCard]:
        return sorted(
            (
                card
                for card in self.fact_cards.values()
                if card.status == "published"
                and card.as_of is not None
                and start <= card.as_of < end
            ),
            key=lambda card: card.as_of,
        )

    def save_brief(self, brief: Brief) -> None:
        self.briefs[brief.brief_date] = brief

    def get_brief_by_date(self, brief_date: str) -> Optional[Brief]:
        return self.briefs.get(brief_date)

    def save_event_entities(self, event_id: str, links: list[EntityLink]) -> None:
        existing = self.event_entities.get(event_id, [])
        for link in links:
            if not any(item.entity_id == link.entity_id for item in existing):
                existing.append(link)
        self.event_entities[event_id] = existing

    def list_event_entities(self, event_id: str) -> list[EntityLink]:
        return list(self.event_entities.get(event_id, []))

    def save_merge_review_task(self, task: MergeReviewTask) -> None:
        self.merge_review_tasks.append(task)

    def get_merge_review_task(self, task_id: str) -> Optional[MergeReviewTask]:
        return next((t for t in self.merge_review_tasks if t.id == task_id), None)

    def list_merge_review_tasks(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[MergeReviewTask]:
        del cursor
        tasks = [t for t in self.merge_review_tasks if status is None or t.status == status]
        tasks.sort(key=lambda t: (t.created_at or datetime.min, t.id))
        if limit:
            tasks = tasks[:limit]
        return tasks

    def update_merge_review_task(self, task: MergeReviewTask) -> None:
        self.merge_review_tasks = [task if t.id == task.id else t for t in self.merge_review_tasks]

    def save_watch_trigger(self, trigger: WatchTrigger) -> None:
        self.watch_triggers[trigger.id] = trigger

    def get_watch_trigger(self, trigger_id: str) -> Optional[WatchTrigger]:
        return self.watch_triggers.get(trigger_id)

    def list_watch_triggers(
        self,
        status: Optional[str] = None,
        event_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[WatchTrigger]:
        triggers = [
            t
            for t in self.watch_triggers.values()
            if (status is None or t.status == status)
            and (event_id is None or t.event_id == event_id)
        ]
        triggers.sort(key=lambda t: (t.created_at or datetime.min, t.id))
        if limit:
            triggers = triggers[:limit]
        return triggers

    def update_watch_trigger(self, trigger: WatchTrigger) -> None:
        if trigger.id not in self.watch_triggers:
            raise KeyError(f"WatchTrigger not found: {trigger.id}")
        self.watch_triggers[trigger.id] = trigger

    def get_event_type_registry(self, type_label: str) -> Optional[EventTypeRegistryEntry]:
        return self.event_type_registry.get(type_label)

    def list_event_type_registry(
        self, status: Optional[str] = None
    ) -> list[EventTypeRegistryEntry]:
        entries = [
            entry
            for entry in self.event_type_registry.values()
            if status is None or entry.status == status
        ]
        entries.sort(key=lambda e: (-e.event_count, e.type_label))
        return entries

    def save_event_type_registry(self, entry: EventTypeRegistryEntry) -> None:
        self.event_type_registry[entry.type_label] = entry

    def increment_event_type_registry_count(self, type_label: str) -> EventTypeRegistryEntry:
        now = datetime.now(timezone.utc)
        existing = self.event_type_registry.get(type_label)
        if existing is None:
            entry = EventTypeRegistryEntry(
                type_label=type_label,
                status="candidate",
                event_count=1,
                created_at=now,
                updated_at=now,
            )
        else:
            entry = replace(existing, event_count=existing.event_count + 1, updated_at=now)
        self.event_type_registry[type_label] = entry
        return entry

    def save_ood_observation(self, observation: OODObservation) -> None:
        self.ood_observations[observation.id] = observation

    def get_ood_observation(self, observation_id: str) -> Optional[OODObservation]:
        return self.ood_observations.get(observation_id)

    def list_ood_observations(
        self, status: Optional[str] = None, limit: Optional[int] = None
    ) -> list[OODObservation]:
        values = [
            item
            for item in self.ood_observations.values()
            if status is None or item.status == status
        ]
        values.sort(key=lambda item: (item.observed_at or datetime.min, item.id), reverse=True)
        return values[:limit] if limit else values

    def update_ood_observation(self, observation: OODObservation) -> None:
        if observation.id not in self.ood_observations:
            raise KeyError(f"OOD observation not found: {observation.id}")
        self.ood_observations[observation.id] = observation

    def save_ood_cluster(self, cluster: OODCluster) -> None:
        self.ood_clusters[cluster.id] = cluster

    def get_ood_cluster(self, cluster_id: str) -> Optional[OODCluster]:
        return self.ood_clusters.get(cluster_id)

    def list_ood_clusters(self, status: Optional[str] = None) -> list[OODCluster]:
        values = [
            item for item in self.ood_clusters.values() if status is None or item.status == status
        ]
        return sorted(values, key=lambda item: item.id)

    def save_ood_feature_snapshot(self, snapshot: OODFeatureSnapshot) -> None:
        self.ood_feature_snapshots[snapshot.id] = snapshot

    def get_ood_feature_snapshot(self, snapshot_id: str) -> Optional[OODFeatureSnapshot]:
        return self.ood_feature_snapshots.get(snapshot_id)

    def save_event_type_proposal(self, proposal: EventTypeProposal) -> None:
        self.event_type_proposals[proposal.id] = proposal

    def get_event_type_proposal(self, proposal_id: str) -> Optional[EventTypeProposal]:
        return self.event_type_proposals.get(proposal_id)

    def list_event_type_proposals(self, status: Optional[str] = None) -> list[EventTypeProposal]:
        values = [
            item
            for item in self.event_type_proposals.values()
            if status is None or item.status == status
        ]
        return sorted(values, key=lambda item: item.created_at or datetime.min, reverse=True)

    def update_event_type_proposal(self, proposal: EventTypeProposal) -> None:
        if proposal.id not in self.event_type_proposals:
            raise KeyError(f"event type proposal not found: {proposal.id}")
        self.event_type_proposals[proposal.id] = proposal

    def save_capability_evaluation(self, evaluation: CapabilityEvaluation) -> None:
        self.capability_evaluations[evaluation.id] = evaluation

    def get_capability_evaluation(self, evaluation_id: str) -> Optional[CapabilityEvaluation]:
        return self.capability_evaluations.get(evaluation_id)

    def list_capability_evaluations(
        self, pack_id: Optional[str] = None
    ) -> list[CapabilityEvaluation]:
        values = [
            item
            for item in self.capability_evaluations.values()
            if pack_id is None or item.pack_id == pack_id
        ]
        return sorted(values, key=lambda item: item.created_at or datetime.min, reverse=True)

    def save_reprocessing_job(self, job: ReprocessingJob) -> None:
        self.reprocessing_jobs[job.id] = job

    def get_reprocessing_job(self, job_id: str) -> Optional[ReprocessingJob]:
        return self.reprocessing_jobs.get(job_id)

    def list_reprocessing_jobs(self) -> list[ReprocessingJob]:
        return sorted(
            self.reprocessing_jobs.values(),
            key=lambda item: item.created_at or datetime.min,
            reverse=True,
        )

    def update_reprocessing_job(self, job: ReprocessingJob) -> None:
        if job.id not in self.reprocessing_jobs:
            raise KeyError(f"reprocessing job not found: {job.id}")
        self.reprocessing_jobs[job.id] = job

    def save_match_decision(self, decision: MatchDecision) -> None:
        self.match_decisions.append(decision)

    def list_match_decisions(self, document_id: str) -> list[MatchDecision]:
        return [
            decision for decision in self.match_decisions if decision.document_id == document_id
        ]

    def save_evidence(self, evidence: EvidenceSpan) -> None:
        self.evidence[evidence.id] = evidence

    def save_claim(self, claim: Claim) -> None:
        self.claims[claim.id] = claim

    def update_claim(self, claim: Claim) -> None:
        self.claims[claim.id] = claim

    def count_claims(self) -> int:
        return len(self.claims)

    def count_claims_with_evidence(self) -> int:
        return sum(1 for claim in self.claims.values() if claim.evidence_ids)

    def find_claim_by_fingerprint(
        self, event_id: str, fingerprint: str, as_of: Optional[datetime] = None
    ) -> Optional[Claim]:
        return next(
            (
                claim
                for claim in self.claims.values()
                if claim.event_id == event_id
                and claim.fingerprint == fingerprint
                and visible_as_of(claim, as_of)
            ),
            None,
        )

    def save_claim_evidence(self, relation: ClaimEvidenceRelation) -> None:
        self.claim_evidence.setdefault(relation.claim_id, []).append(relation)

    def save_conflict(self, conflict: ConflictRecord) -> None:
        self.conflicts.append(conflict)

    def get_conflict(self, conflict_id: str) -> Optional[ConflictRecord]:
        return next((c for c in self.conflicts if c.id == conflict_id), None)

    def update_conflict(self, conflict: ConflictRecord) -> None:
        self.conflicts = [conflict if c.id == conflict.id else c for c in self.conflicts]

    def list_claim_evidence(self, claim_id: str) -> list[ClaimEvidenceRelation]:
        return list(self.claim_evidence.get(claim_id, []))

    def list_conflicts_for_event(
        self, event_id: str, as_of: Optional[datetime] = None
    ) -> list[ConflictRecord]:
        # ConflictRecord 当前无时间戳字段，as_of 过滤暂不适用；保留参数供后续扩展。
        del as_of
        return [conflict for conflict in self.conflicts if conflict.event_id == event_id]

    def save_fact_card(self, fact_card: FactCard) -> None:
        existing = self.fact_cards.get(fact_card.id)
        if existing is not None:
            raise ReportVersionConflict("REPORT_VERSION_IMMUTABLE")
        if any(
            card.event_id == fact_card.event_id and card.version == fact_card.version
            for card in self.fact_cards.values()
        ):
            raise ReportVersionConflict("REPORT_VERSION_CONFLICT")
        self.fact_cards[fact_card.id] = fact_card

    def save_impact_analysis(self, impact_analysis: ImpactAnalysis) -> None:
        existing = self.impact_analyses.get(impact_analysis.id)
        if existing is not None:
            raise ReportVersionConflict("IMPACT_ANALYSIS_IMMUTABLE")
        if any(
            ia.event_id == impact_analysis.event_id and ia.version == impact_analysis.version
            for ia in self.impact_analyses.values()
        ):
            raise ReportVersionConflict("IMPACT_ANALYSIS_VERSION_CONFLICT")
        self.impact_analyses[impact_analysis.id] = impact_analysis

    def get_impact_analysis(self, impact_analysis_id: str) -> Optional[ImpactAnalysis]:
        return self.impact_analyses.get(impact_analysis_id)

    def get_latest_impact_analysis_for_event(self, event_id: str) -> Optional[ImpactAnalysis]:
        versions = [
            ia
            for ia in self.impact_analyses.values()
            if ia.event_id == event_id and ia.status != "superseded"
        ]
        if not versions:
            return None
        return max(versions, key=lambda ia: (ia.version, ia.created_at or datetime.min))

    def list_impact_analyses_for_event(
        self, event_id: str, limit: Optional[int] = None
    ) -> list[ImpactAnalysis]:
        items = sorted(
            (ia for ia in self.impact_analyses.values() if ia.event_id == event_id),
            key=lambda ia: (ia.version, ia.created_at or datetime.min),
            reverse=True,
        )
        return items[:limit] if limit is not None else items

    def update_impact_analysis(self, impact_analysis: ImpactAnalysis) -> None:
        if impact_analysis.id not in self.impact_analyses:
            raise KeyError(f"impact_analysis not found: {impact_analysis.id}")
        self.impact_analyses[impact_analysis.id] = impact_analysis

    def save_preliminary_assessment(self, assessment: EventPreliminaryAssessment) -> None:
        existing = self.preliminary_assessments.get(assessment.id)
        if existing is not None:
            raise ReportVersionConflict("PRELIMINARY_ASSESSMENT_IMMUTABLE")
        if any(
            item.event_id == assessment.event_id and item.version == assessment.version
            for item in self.preliminary_assessments.values()
        ):
            raise ReportVersionConflict("PRELIMINARY_ASSESSMENT_VERSION_CONFLICT")
        self.preliminary_assessments[assessment.id] = assessment

    def get_preliminary_assessment(
        self, assessment_id: str
    ) -> Optional[EventPreliminaryAssessment]:
        return self.preliminary_assessments.get(assessment_id)

    def get_latest_preliminary_assessment_for_event(
        self, event_id: str
    ) -> Optional[EventPreliminaryAssessment]:
        items = [
            item
            for item in self.preliminary_assessments.values()
            if item.event_id == event_id and item.status != "superseded"
        ]
        return max(
            items, key=lambda item: (item.version, item.created_at or datetime.min), default=None
        )

    def list_preliminary_assessments_for_event(
        self, event_id: str, limit: Optional[int] = None
    ) -> list[EventPreliminaryAssessment]:
        items = sorted(
            (item for item in self.preliminary_assessments.values() if item.event_id == event_id),
            key=lambda item: (item.version, item.created_at or datetime.min),
            reverse=True,
        )
        return items[:limit] if limit is not None else items

    def update_preliminary_assessment(self, assessment: EventPreliminaryAssessment) -> None:
        if assessment.id not in self.preliminary_assessments:
            raise KeyError(f"preliminary_assessment not found: {assessment.id}")
        self.preliminary_assessments[assessment.id] = assessment

    def get_impact_graph_layout(
        self, analysis_id: str, user_id: str
    ) -> Optional[ImpactGraphLayout]:
        return self.impact_graph_layouts.get((analysis_id, user_id))

    def save_impact_graph_layout(self, layout: ImpactGraphLayout) -> None:
        self.impact_graph_layouts[(layout.analysis_id, layout.user_id)] = layout

    def delete_impact_graph_layout(self, analysis_id: str, user_id: str) -> None:
        self.impact_graph_layouts.pop((analysis_id, user_id), None)

    def save_impact_target(self, target: ImpactTargetDefinition) -> None:
        existing = self.find_impact_target(
            target.target_type, target.target_code, target.taxonomy_version
        )
        if existing and existing.id != target.id:
            raise ReportVersionConflict("IMPACT_TARGET_CONFLICT")
        self.impact_targets[target.id] = target

    def get_impact_target(self, target_id: str) -> Optional[ImpactTargetDefinition]:
        return self.impact_targets.get(target_id)

    def find_impact_target(
        self, target_type: str, target_code: str, taxonomy_version: str = "default-v1"
    ) -> Optional[ImpactTargetDefinition]:
        return next(
            (
                item
                for item in self.impact_targets.values()
                if item.target_type == target_type
                and item.target_code == target_code
                and item.taxonomy_version == taxonomy_version
            ),
            None,
        )

    def list_impact_targets(
        self, target_type: Optional[str] = None
    ) -> list[ImpactTargetDefinition]:
        items = list(self.impact_targets.values())
        return sorted(
            [item for item in items if target_type is None or item.target_type == target_type],
            key=lambda item: item.canonical_name,
        )

    def save_market_instrument(self, value: MarketInstrument) -> None:
        self.market_instruments[value.id] = value

    def get_market_instrument(self, instrument_id: str) -> Optional[MarketInstrument]:
        return self.market_instruments.get(instrument_id)

    def list_market_instruments(self, active: Optional[bool] = None) -> list[MarketInstrument]:
        return sorted(
            [
                item
                for item in self.market_instruments.values()
                if active is None or item.active is active
            ],
            key=lambda item: (item.market, item.instrument_type, item.symbol),
        )

    def save_industry_taxonomy(self, value: IndustryTaxonomy) -> None:
        self.industry_taxonomies[value.id] = value

    def list_industry_taxonomies(self, status: Optional[str] = None) -> list[IndustryTaxonomy]:
        return sorted(
            [
                item
                for item in self.industry_taxonomies.values()
                if status is None or item.status == status
            ],
            key=lambda item: (item.standard, item.version),
        )

    def save_industry_classification(self, value: IndustryClassification) -> None:
        self.industry_classifications[value.id] = value

    def list_industry_classifications(
        self, taxonomy_id: Optional[str] = None
    ) -> list[IndustryClassification]:
        return sorted(
            [
                item
                for item in self.industry_classifications.values()
                if taxonomy_id is None or item.taxonomy_id == taxonomy_id
            ],
            key=lambda item: (item.taxonomy_id, item.level, item.code),
        )

    def save_instrument_industry_membership(self, value: InstrumentIndustryMembership) -> None:
        self.instrument_industry_memberships[value.id] = value

    def list_instrument_industry_memberships(
        self, instrument_id: Optional[str] = None, status: Optional[str] = None
    ) -> list[InstrumentIndustryMembership]:
        return sorted(
            [
                item
                for item in self.instrument_industry_memberships.values()
                if (instrument_id is None or item.instrument_id == instrument_id)
                and (status is None or item.status == status)
            ],
            key=lambda item: (item.instrument_id, item.taxonomy_id, item.industry_code),
        )

    def save_impact_target_mapping(self, value: ImpactTargetMapping) -> None:
        self.impact_target_mappings[value.id] = value

    def get_impact_target_mapping(self, mapping_id: str) -> Optional[ImpactTargetMapping]:
        return self.impact_target_mappings.get(mapping_id)

    def update_impact_target_mapping(self, value: ImpactTargetMapping) -> None:
        if value.id not in self.impact_target_mappings:
            raise KeyError(f"impact target mapping not found: {value.id}")
        self.impact_target_mappings[value.id] = value

    def list_impact_target_mappings(
        self, target_id: Optional[str] = None, status: Optional[str] = None
    ) -> list[ImpactTargetMapping]:
        return sorted(
            [
                item
                for item in self.impact_target_mappings.values()
                if (target_id is None or item.target_id == target_id)
                and (status is None or item.status == status)
            ],
            key=lambda item: (item.target_id, item.mapping_type, item.mapping_code),
        )

    def save_market_master_data_import_run(self, value: MarketMasterDataImportRun) -> None:
        if self.find_market_master_data_import_run_by_hash(value.source_hash) is None:
            self.market_master_data_import_runs[value.id] = value

    def get_market_master_data_import_run(self, run_id: str) -> Optional[MarketMasterDataImportRun]:
        return self.market_master_data_import_runs.get(run_id)

    def find_market_master_data_import_run_by_hash(
        self, source_hash: str
    ) -> Optional[MarketMasterDataImportRun]:
        return next(
            (
                item
                for item in self.market_master_data_import_runs.values()
                if item.source_hash == source_hash
            ),
            None,
        )

    def update_market_master_data_import_run(self, value: MarketMasterDataImportRun) -> None:
        if value.id not in self.market_master_data_import_runs:
            raise KeyError(f"market master import run not found: {value.id}")
        self.market_master_data_import_runs[value.id] = value

    def list_market_master_data_import_runs(self) -> list[MarketMasterDataImportRun]:
        return sorted(
            self.market_master_data_import_runs.values(),
            key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

    def save_event_impact_relation(self, relation: EventImpactRelation) -> None:
        self.event_impact_relations[relation.id] = relation

    def list_event_impact_relations(
        self, event_id: Optional[str] = None
    ) -> list[EventImpactRelation]:
        return [
            item
            for item in self.event_impact_relations.values()
            if event_id is None
            or item.source_event_id == event_id
            or item.target_event_id == event_id
        ]

    def save_impact_contribution(self, contribution: ImpactContribution) -> None:
        self.impact_contributions[contribution.id] = contribution

    def list_impact_contributions(
        self, target_id: Optional[str] = None
    ) -> list[ImpactContribution]:
        return [
            item
            for item in self.impact_contributions.values()
            if target_id is None or item.target_id == target_id
        ]

    def save_impact_dimension_contribution(self, item: ImpactDimensionContribution) -> None:
        self.impact_dimension_contributions[item.id] = item

    def list_impact_dimension_contributions(
        self, contribution_id: Optional[str] = None
    ) -> list[ImpactDimensionContribution]:
        return [
            item
            for item in self.impact_dimension_contributions.values()
            if contribution_id is None or item.contribution_id == contribution_id
        ]

    def save_target_impact_snapshot(
        self, snapshot: TargetImpactSnapshot, contributions: list[TargetImpactSnapshotContribution]
    ) -> None:
        self.target_impact_snapshots[snapshot.id] = snapshot
        self.target_impact_snapshot_contributions = [
            item
            for item in self.target_impact_snapshot_contributions
            if item.snapshot_id != snapshot.id
        ]
        self.target_impact_snapshot_contributions.extend(contributions)

    def get_latest_target_impact_snapshot(
        self,
        target_id: str,
        horizon: Optional[str] = None,
        scenario_set_id: str = "baseline",
        as_of: Optional[datetime] = None,
    ) -> Optional[TargetImpactSnapshot]:
        items = [
            item
            for item in self.target_impact_snapshots.values()
            if item.target_id == target_id
            and item.scenario_set_id == scenario_set_id
            and (horizon is None or item.horizon == horizon)
            and (as_of is None or item.as_of <= as_of)
        ]
        return max(
            items, key=lambda item: (item.as_of, item.created_at or datetime.min), default=None
        )

    def list_target_impact_snapshot_contributions(
        self, snapshot_id: str
    ) -> list[TargetImpactSnapshotContribution]:
        return [
            item
            for item in self.target_impact_snapshot_contributions
            if item.snapshot_id == snapshot_id
        ]

    def save_market_forecast_run(self, value: MarketForecastRun) -> None:
        existing = self.find_market_forecast_run_by_source_hash(value.source_hash)
        if existing is None:
            self.market_forecast_runs[value.id] = value

    def get_market_forecast_run(self, forecast_id: str) -> Optional[MarketForecastRun]:
        return self.market_forecast_runs.get(forecast_id)

    def find_market_forecast_run_by_source_hash(
        self, source_hash: str
    ) -> Optional[MarketForecastRun]:
        return next(
            (
                item
                for item in self.market_forecast_runs.values()
                if item.source_hash == source_hash
            ),
            None,
        )

    def list_market_forecast_runs(
        self,
        instrument_id: Optional[str] = None,
        horizon: Optional[int] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 500,
    ) -> list[MarketForecastRun]:
        values = [
            item
            for item in self.market_forecast_runs.values()
            if (instrument_id is None or item.instrument_id == instrument_id)
            and (horizon is None or item.horizon == horizon)
            and (start is None or item.as_of >= start)
            and (end is None or item.as_of <= end)
        ]
        return sorted(values, key=lambda item: (item.as_of, item.id), reverse=True)[:limit]

    def save_market_forecast_outcome(self, value: MarketForecastOutcome) -> None:
        if self.get_market_forecast_outcome(value.forecast_id) is None:
            self.market_forecast_outcomes[value.id] = value

    def get_market_forecast_outcome(self, forecast_id: str) -> Optional[MarketForecastOutcome]:
        return next(
            (
                item
                for item in self.market_forecast_outcomes.values()
                if item.forecast_id == forecast_id
            ),
            None,
        )

    def list_market_forecast_outcomes(
        self, forecast_ids: Optional[list[str]] = None
    ) -> list[MarketForecastOutcome]:
        allowed = set(forecast_ids) if forecast_ids is not None else None
        return sorted(
            [
                item
                for item in self.market_forecast_outcomes.values()
                if allowed is None or item.forecast_id in allowed
            ],
            key=lambda item: (item.outcome_observed_at, item.id),
            reverse=True,
        )

    def save_market_calibration_version(self, value: MarketCalibrationVersion) -> None:
        existing = next(
            (
                item
                for item in self.market_calibration_versions.values()
                if (item.model_key, item.version, item.horizon, item.market)
                == (value.model_key, value.version, value.horizon, value.market)
            ),
            None,
        )
        if existing is None:
            self.market_calibration_versions[value.id] = value

    def get_market_calibration_version(
        self, calibration_id: str
    ) -> Optional[MarketCalibrationVersion]:
        return self.market_calibration_versions.get(calibration_id)

    def update_market_calibration_version(self, value: MarketCalibrationVersion) -> None:
        if value.id not in self.market_calibration_versions:
            raise KeyError(f"market calibration not found: {value.id}")
        self.market_calibration_versions[value.id] = value

    def list_market_calibration_versions(
        self,
        model_key: Optional[str] = None,
        market: Optional[str] = None,
        horizon: Optional[int] = None,
        status: Optional[str] = None,
    ) -> list[MarketCalibrationVersion]:
        return sorted(
            [
                item
                for item in self.market_calibration_versions.values()
                if (model_key is None or item.model_key == model_key)
                and (market is None or item.market == market)
                and (horizon is None or item.horizon == horizon)
                and (status is None or item.status == status)
            ],
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

    def save_forward_impact_window(self, value: ForwardImpactWindow) -> None:
        self.forward_impact_windows[value.id] = value

    def get_forward_impact_window(self, window_id: str) -> Optional[ForwardImpactWindow]:
        return self.forward_impact_windows.get(window_id)

    def save_forward_catalyst(self, value: ForwardCatalyst) -> None:
        self.forward_catalysts[value.id] = value

    def get_forward_catalyst(self, catalyst_id: str) -> Optional[ForwardCatalyst]:
        return self.forward_catalysts.get(catalyst_id)

    def list_forward_catalysts(self, target_id: Optional[str] = None) -> list[ForwardCatalyst]:
        return [
            item
            for item in self.forward_catalysts.values()
            if target_id is None or item.target_id == target_id
        ]

    def save_forward_contribution(self, value: ForwardImpactContribution) -> None:
        self.forward_contributions[value.id] = value

    def list_forward_contributions(self, window_id: str) -> list[ForwardImpactContribution]:
        return [item for item in self.forward_contributions.values() if item.window_id == window_id]

    def save_forward_points(self, values: list[ForwardImpactPoint]) -> None:
        for value in values:
            self.forward_points[value.id] = value

    def list_forward_points(
        self, window_id: str, scenario_id: str = "baseline"
    ) -> list[ForwardImpactPoint]:
        return sorted(
            [
                item
                for item in self.forward_points.values()
                if item.window_id == window_id and item.scenario_id == scenario_id
            ],
            key=lambda item: item.point_at,
        )

    def save_future_event(self, value: FutureEvent) -> None:
        self.future_events[value.id] = value

    def get_future_event(self, event_id: str) -> Optional[FutureEvent]:
        return self.future_events.get(event_id)

    def list_future_events(self) -> list[FutureEvent]:
        return list(self.future_events.values())

    def save_future_event_revision(self, value: FutureEventRevision) -> None:
        self.future_event_revisions[value.id] = value
        event = self.future_events.get(value.future_event_id)
        if event and (
            event.current_revision_id is None
            or value.revision_no >= self._revision_no(event.current_revision_id)
        ):
            self.future_events[event.id] = FutureEvent(
                **{**event.__dict__, "current_revision_id": value.id}
            )

    def _revision_no(self, revision_id: str) -> int:
        revision = self.future_event_revisions.get(revision_id)
        return revision.revision_no if revision else -1

    def get_future_event_revision(self, revision_id: str) -> Optional[FutureEventRevision]:
        return self.future_event_revisions.get(revision_id)

    def list_future_event_revisions(self, event_id: str) -> list[FutureEventRevision]:
        return sorted(
            [
                item
                for item in self.future_event_revisions.values()
                if item.future_event_id == event_id
            ],
            key=lambda item: item.revision_no,
        )

    def save_future_event_target_impact(self, value: FutureEventTargetImpact) -> None:
        self.future_event_target_impacts[value.id] = value

    def list_future_event_target_impacts(
        self, event_id: Optional[str] = None, target_id: Optional[str] = None
    ) -> list[FutureEventTargetImpact]:
        return [
            item
            for item in self.future_event_target_impacts.values()
            if (event_id is None or item.future_event_id == event_id)
            and (target_id is None or item.target_id == target_id)
        ]

    # Agent Runtime (DD-80)
    def save_agent_registration(self, registration: AgentRegistration) -> None:
        self.agent_registrations[registration.agent_key] = registration

    def get_agent_registration(self, agent_key: str) -> Optional[AgentRegistration]:
        return self.agent_registrations.get(agent_key)

    def list_agent_registrations(self) -> list[AgentRegistration]:
        return list(self.agent_registrations.values())

    def save_research_plan(self, plan: ResearchPlan) -> None:
        self.research_plans[plan.id] = plan

    def get_research_plan(self, plan_id: str) -> Optional[ResearchPlan]:
        return self.research_plans.get(plan_id)

    def get_research_plan_by_workflow(self, workflow_id: str) -> Optional[ResearchPlan]:
        return next(
            (plan for plan in self.research_plans.values() if plan.workflow_id == workflow_id),
            None,
        )

    def list_research_plans(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[ResearchPlan]:
        values = list(self.research_plans.values())
        if status:
            values = [p for p in values if p.status == status]
        return _paginate(values, cursor, limit, lambda value: value.created_at)

    def update_research_plan(self, plan: ResearchPlan) -> None:
        if plan.id not in self.research_plans:
            raise KeyError(f"research_plan not found: {plan.id}")
        self.research_plans[plan.id] = plan

    def save_research_task(self, task: ResearchTask) -> None:
        self.research_tasks[task.id] = task

    def get_research_task(self, task_id: str) -> Optional[ResearchTask]:
        return self.research_tasks.get(task_id)

    def list_research_tasks(self, plan_id: str) -> list[ResearchTask]:
        return [task for task in self.research_tasks.values() if task.plan_id == plan_id]

    def update_research_task(self, task: ResearchTask) -> None:
        if task.id not in self.research_tasks:
            raise KeyError(f"research_task not found: {task.id}")
        self.research_tasks[task.id] = task

    def list_events(
        self,
        as_of: Optional[datetime] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        *,
        event_types: Optional[list[str]] = None,
        entity_ids: Optional[list[str]] = None,
        occurred_from: Optional[datetime] = None,
        occurred_to: Optional[datetime] = None,
    ) -> list[Event]:
        values = [event for event in self.events.values() if visible_as_of(event, as_of)]
        if event_types:
            values = [event for event in values if event.event_type in event_types]
        if entity_ids:
            entity_set = set(entity_ids)
            values = [event for event in values if entity_set.intersection(event.entity_ids)]
        if occurred_from is not None:
            values = [event for event in values if event.occurred_at >= occurred_from]
        if occurred_to is not None:
            values = [event for event in values if event.occurred_at <= occurred_to]
        return _paginate(values, cursor, limit, lambda value: value.occurred_at)

    def get_document(
        self, document_id: str, *, include_deleted: bool = False
    ) -> Optional[Document]:
        document = self.documents.get(document_id)
        if document is None:
            return None
        if document.deleted_at is not None and not include_deleted:
            return None
        return document

    def get_event(self, event_id: str) -> Optional[Event]:
        return self.events.get(event_id)

    def find_event_by_document(
        self, document_id: str, as_of: Optional[datetime] = None
    ) -> Optional[Event]:
        return next(
            (
                event
                for event in self.events.values()
                if document_id in event.document_ids and visible_as_of(event, as_of)
            ),
            None,
        )

    def get_evidence(
        self, evidence_id: str, *, include_deleted: bool = False
    ) -> Optional[EvidenceSpan]:
        evidence = self.evidence.get(evidence_id)
        if evidence is None:
            return None
        if evidence.deleted_at is not None and not include_deleted:
            return None
        if not include_deleted:
            document = self.documents.get(evidence.document_id)
            if document is not None and document.deleted_at is not None:
                return None
        return evidence

    def set_document_retention_hold(self, document_id: str, hold: bool) -> Document:
        document = self.documents.get(document_id)
        if document is None:
            raise KeyError(f"Document not found: {document_id}")
        updated = replace(document, retention_hold=hold)
        self.update_document(updated)
        return updated

    def soft_delete_document(
        self, document_id: str, *, deleted_at: Optional[datetime] = None
    ) -> Document:
        document = self.documents.get(document_id)
        if document is None:
            raise KeyError(f"Document not found: {document_id}")
        if document.deleted_at is not None:
            return document
        if document.retention_hold:
            raise RetentionHoldError(document_id)
        when = deleted_at or datetime.now(timezone.utc)
        updated = replace(document, deleted_at=when)
        self.update_document(updated)
        for evidence in list(self.evidence.values()):
            if evidence.document_id == document_id and evidence.deleted_at is None:
                self.evidence[evidence.id] = replace(evidence, deleted_at=when)
        return updated

    def purge_document(
        self,
        document_id: str,
        *,
        purged_at: Optional[datetime] = None,
        min_soft_delete_age_seconds: int = 0,
    ) -> Document:
        document = self.documents.get(document_id)
        if document is None:
            raise KeyError(f"Document not found: {document_id}")
        if document.purged_at is not None:
            return document
        if document.retention_hold:
            raise RetentionHoldError(document_id)
        if document.deleted_at is None:
            raise DocumentNotSoftDeletedError(document_id)
        when = purged_at or datetime.now(timezone.utc)
        _assert_purge_retention_window(
            document_id,
            document.deleted_at,
            min_soft_delete_age_seconds=min_soft_delete_age_seconds,
            now=when,
        )
        updated = replace(
            document,
            title="[purged]",
            content="",
            purged_at=when,
        )
        self.update_document(updated)
        for evidence_id, evidence in list(self.evidence.items()):
            if evidence.document_id == document_id:
                del self.evidence[evidence_id]
        return updated

    def list_documents_eligible_for_purge(
        self,
        *,
        deleted_before: datetime,
        limit: int = 100,
    ) -> list[Document]:
        values = [
            document
            for document in self.documents.values()
            if document.deleted_at is not None
            and document.purged_at is None
            and not document.retention_hold
            and document.deleted_at <= deleted_before
        ]
        values.sort(key=lambda item: (item.deleted_at or datetime.min, item.id))
        return values[: max(1, limit)]

    def get_claim(self, claim_id: str) -> Optional[Claim]:
        return self.claims.get(claim_id)

    def get_claims_for_event(self, event_id: str, as_of: Optional[datetime] = None) -> list[Claim]:
        return [
            claim
            for claim in self.claims.values()
            if claim.event_id == event_id and visible_as_of(claim, as_of)
        ]

    def get_fact_card(self, fact_card_id: str) -> Optional[FactCard]:
        return self.fact_cards.get(fact_card_id)

    def get_fact_card_for_event(
        self, event_id: str, as_of: Optional[datetime] = None
    ) -> Optional[FactCard]:
        cards = [
            card
            for card in self.fact_cards.values()
            if card.event_id == event_id and visible_as_of(card, as_of)
        ]
        return max(cards, key=lambda card: card.version) if cards else None

    def get_idempotent(self, key: str) -> Optional[PipelineResultReference]:
        return self._idempotency.get(key)

    def save_idempotent(self, key: str, value: PipelineResultReference) -> None:
        self._idempotency[key] = value

    def get_api_idempotent(self, key: str) -> Optional[ApiIdempotencyRecord]:
        return self._api_idempotency.get(key)

    def save_api_idempotent(self, key: str, value: ApiIdempotencyRecord) -> None:
        self._api_idempotency[key] = value

    def add_outbox(self, event_type: str, aggregate_id: str, payload: dict) -> None:
        self.outbox.append(
            {
                "id": new_id("msg"),
                "event_type": event_type,
                "aggregate_id": aggregate_id,
                "payload": payload,
                "trace_id": new_id("trc"),
                "attempts": 0,
                "created_at": datetime.now(timezone.utc),
                "published_at": None,
                "next_attempt_at": None,
                "last_error": None,
                "dead_lettered_at": None,
            }
        )

    def list_outbox(
        self,
        dead_lettered: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[OutboxMessage]:
        values = [_outbox_message_dict(value) for value in self.outbox]
        if dead_lettered is not None:
            values = [
                value for value in values if (value.dead_lettered_at is not None) == dead_lettered
            ]
        return _paginate(values, cursor, limit, lambda value: value.created_at)

    def get_outbox(self, outbox_id: str) -> Optional[OutboxMessage]:
        value = next((item for item in self.outbox if item["id"] == outbox_id), None)
        return _outbox_message_dict(value) if value else None

    def retry_outbox(self, outbox_id: str) -> None:
        with self._lock:
            value = next((item for item in self.outbox if item["id"] == outbox_id), None)
            if value is None:
                raise KeyError(f"Outbox message not found: {outbox_id}")
            value["attempts"] = 0
            value["next_attempt_at"] = None
            value["last_error"] = None
            value["dead_lettered_at"] = None

    def list_pending_outbox(
        self, limit: int, now: Optional[datetime] = None
    ) -> list[OutboxMessage]:
        current = now or datetime.now(timezone.utc)
        pending = [
            value
            for value in self.outbox
            if value["published_at"] is None
            and value["dead_lettered_at"] is None
            and (value["next_attempt_at"] is None or value["next_attempt_at"] <= current)
        ][:limit]
        return [
            OutboxMessage(
                id=value["id"],
                event_type=value["event_type"],
                aggregate_id=value["aggregate_id"],
                payload=value["payload"],
                trace_id=value["trace_id"],
                attempts=value["attempts"],
            )
            for value in pending
        ]

    def list_pending_outbox_by_event_type(
        self, event_type: str, limit: int, now: Optional[datetime] = None
    ) -> list[OutboxMessage]:
        current = now or datetime.now(timezone.utc)
        pending = [
            value
            for value in self.outbox
            if value["event_type"] == event_type
            and value["published_at"] is None
            and value["dead_lettered_at"] is None
            and (value["next_attempt_at"] is None or value["next_attempt_at"] <= current)
        ][:limit]
        return [
            OutboxMessage(
                id=value["id"],
                event_type=value["event_type"],
                aggregate_id=value["aggregate_id"],
                payload=value["payload"],
                trace_id=value["trace_id"],
                attempts=value["attempts"],
            )
            for value in pending
        ]

    def mark_outbox_published(self, message_id: str, published_at: datetime) -> None:
        with self._lock:
            message = next(value for value in self.outbox if value["id"] == message_id)
            message["published_at"] = published_at

    def mark_outbox_failed(self, message_id: str, error: str, next_attempt_at: datetime) -> None:
        with self._lock:
            message = next(value for value in self.outbox if value["id"] == message_id)
            message["attempts"] += 1
            message["last_error"] = error
            message["next_attempt_at"] = next_attempt_at

    def mark_outbox_dead_lettered(
        self, message_id: str, error: str, dead_lettered_at: datetime
    ) -> None:
        with self._lock:
            message = next(value for value in self.outbox if value["id"] == message_id)
            message["attempts"] += 1
            message["last_error"] = error
            message["dead_lettered_at"] = dead_lettered_at

    def is_inbox_processed(self, consumer: str, message_id: str) -> bool:
        return (consumer, message_id) in self.inbox

    def save_inbox_processed(
        self, consumer: str, message_id: str, result: Optional[dict] = None
    ) -> None:
        self.inbox.add((consumer, message_id))


class SqlAlchemyRepository:
    def __init__(
        self,
        database_url: str,
        *,
        schema_translate_map: Optional[dict[str, Optional[str]]] = None,
    ) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
        if schema_translate_map:
            engine = engine.execution_options(schema_translate_map=schema_translate_map)
        self.engine: Engine = engine
        self.session_factory = sessionmaker(engine, expire_on_commit=False)

    def create_schema_for_tests(self) -> None:
        """Test-only shortcut; deployed databases must be initialized by Alembic."""
        Base.metadata.create_all(self.engine)

    @contextmanager
    def transaction(self) -> Iterator["SqlAlchemyTransaction"]:
        with self.session_factory() as session:
            with session.begin():
                yield SqlAlchemyTransaction(session)

    def _read(self, callback):
        with self.session_factory() as session:
            return callback(SqlAlchemyTransaction(session))

    def list_events(
        self,
        as_of: Optional[datetime] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        *,
        event_types: Optional[list[str]] = None,
        entity_ids: Optional[list[str]] = None,
        occurred_from: Optional[datetime] = None,
        occurred_to: Optional[datetime] = None,
    ) -> list[Event]:
        return self._read(
            lambda repository: repository.list_events(
                as_of,
                limit,
                cursor,
                event_types=event_types,
                entity_ids=entity_ids,
                occurred_from=occurred_from,
                occurred_to=occurred_to,
            )
        )

    def get_event(self, event_id: str) -> Optional[Event]:
        return self._read(lambda repository: repository.get_event(event_id))

    def save_review_task(self, value: ReviewTask) -> None:
        with self.transaction() as repository:
            repository.save_review_task(value)

    def get_review_policy(self) -> ReviewPolicy:
        return self._read(lambda repository: repository.get_review_policy())

    def save_review_policy(self, policy: ReviewPolicy) -> None:
        with self.transaction() as repository:
            repository.save_review_policy(policy)

    def get_source_collection_config(self) -> SourceCollectionConfig:
        return self._read(lambda repository: repository.get_source_collection_config())

    def save_source_collection_config(self, config: SourceCollectionConfig) -> None:
        with self.transaction() as repository:
            repository.save_source_collection_config(config)

    def save_auto_review_attempt(self, attempt: AutoReviewAttempt) -> None:
        with self.transaction() as repository:
            repository.save_auto_review_attempt(attempt)

    def list_auto_review_attempts(self, task_id: str, limit: int = 20) -> list[AutoReviewAttempt]:
        return self._read(lambda repository: repository.list_auto_review_attempts(task_id, limit))

    def get_claim(self, claim_id: str) -> Optional[Claim]:
        return self._read(lambda repository: repository.get_claim(claim_id))

    def list_claim_evidence(self, claim_id: str) -> list[ClaimEvidenceRelation]:
        return self._read(lambda repository: repository.list_claim_evidence(claim_id))

    def get_claims_for_event(self, event_id: str, as_of: Optional[datetime] = None) -> list[Claim]:
        return self._read(lambda repository: repository.get_claims_for_event(event_id, as_of))

    def save_entity(self, value: Entity) -> None:
        with self.transaction() as repository:
            repository.save_entity(value)

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return self._read(lambda repository: repository.get_entity(entity_id))

    def get_fact_card(self, fact_card_id: str) -> Optional[FactCard]:
        return self._read(lambda repository: repository.get_fact_card(fact_card_id))

    def save_impact_analysis(self, impact_analysis: ImpactAnalysis) -> None:
        with self.transaction() as repository:
            repository.save_impact_analysis(impact_analysis)

    def get_impact_analysis(self, impact_analysis_id: str) -> Optional[ImpactAnalysis]:
        return self._read(lambda repository: repository.get_impact_analysis(impact_analysis_id))

    def get_latest_impact_analysis_for_event(self, event_id: str) -> Optional[ImpactAnalysis]:
        return self._read(
            lambda repository: repository.get_latest_impact_analysis_for_event(event_id)
        )

    def list_impact_analyses_for_event(
        self, event_id: str, limit: Optional[int] = None
    ) -> list[ImpactAnalysis]:
        return self._read(
            lambda repository: repository.list_impact_analyses_for_event(event_id, limit)
        )

    def update_impact_analysis(self, impact_analysis: ImpactAnalysis) -> None:
        with self.transaction() as repository:
            repository.update_impact_analysis(impact_analysis)

    def save_preliminary_assessment(self, assessment: EventPreliminaryAssessment) -> None:
        with self.transaction() as repository:
            repository.save_preliminary_assessment(assessment)

    def get_preliminary_assessment(
        self, assessment_id: str
    ) -> Optional[EventPreliminaryAssessment]:
        return self._read(lambda repository: repository.get_preliminary_assessment(assessment_id))

    def get_latest_preliminary_assessment_for_event(
        self, event_id: str
    ) -> Optional[EventPreliminaryAssessment]:
        return self._read(
            lambda repository: repository.get_latest_preliminary_assessment_for_event(event_id)
        )

    def list_preliminary_assessments_for_event(
        self, event_id: str, limit: Optional[int] = None
    ) -> list[EventPreliminaryAssessment]:
        return self._read(
            lambda repository: repository.list_preliminary_assessments_for_event(event_id, limit)
        )

    def update_preliminary_assessment(self, assessment: EventPreliminaryAssessment) -> None:
        with self.transaction() as repository:
            repository.update_preliminary_assessment(assessment)

    def get_impact_graph_layout(
        self, analysis_id: str, user_id: str
    ) -> Optional[ImpactGraphLayout]:
        return self._read(
            lambda repository: repository.get_impact_graph_layout(analysis_id, user_id)
        )

    def save_impact_graph_layout(self, layout: ImpactGraphLayout) -> None:
        with self.transaction() as repository:
            repository.save_impact_graph_layout(layout)

    def delete_impact_graph_layout(self, analysis_id: str, user_id: str) -> None:
        with self.transaction() as repository:
            repository.delete_impact_graph_layout(analysis_id, user_id)

    def save_impact_target(self, target: ImpactTargetDefinition) -> None:
        with self.transaction() as repository:
            repository.save_impact_target(target)

    def get_impact_target(self, target_id: str) -> Optional[ImpactTargetDefinition]:
        return self._read(lambda repository: repository.get_impact_target(target_id))

    def find_impact_target(
        self, target_type: str, target_code: str, taxonomy_version: str = "default-v1"
    ) -> Optional[ImpactTargetDefinition]:
        return self._read(
            lambda repository: repository.find_impact_target(
                target_type, target_code, taxonomy_version
            )
        )

    def list_impact_targets(
        self, target_type: Optional[str] = None
    ) -> list[ImpactTargetDefinition]:
        return self._read(lambda repository: repository.list_impact_targets(target_type))

    def save_market_instrument(self, value: MarketInstrument) -> None:
        with self.transaction() as repository:
            repository.save_market_instrument(value)

    def get_market_instrument(self, instrument_id: str) -> Optional[MarketInstrument]:
        return self._read(lambda repository: repository.get_market_instrument(instrument_id))

    def list_market_instruments(self, active: Optional[bool] = None) -> list[MarketInstrument]:
        return self._read(lambda repository: repository.list_market_instruments(active))

    def save_industry_taxonomy(self, value: IndustryTaxonomy) -> None:
        with self.transaction() as repository:
            repository.save_industry_taxonomy(value)

    def list_industry_taxonomies(self, status: Optional[str] = None) -> list[IndustryTaxonomy]:
        return self._read(lambda repository: repository.list_industry_taxonomies(status))

    def save_industry_classification(self, value: IndustryClassification) -> None:
        with self.transaction() as repository:
            repository.save_industry_classification(value)

    def list_industry_classifications(
        self, taxonomy_id: Optional[str] = None
    ) -> list[IndustryClassification]:
        return self._read(lambda repository: repository.list_industry_classifications(taxonomy_id))

    def save_instrument_industry_membership(self, value: InstrumentIndustryMembership) -> None:
        with self.transaction() as repository:
            repository.save_instrument_industry_membership(value)

    def list_instrument_industry_memberships(
        self, instrument_id: Optional[str] = None, status: Optional[str] = None
    ) -> list[InstrumentIndustryMembership]:
        return self._read(
            lambda repository: repository.list_instrument_industry_memberships(
                instrument_id, status
            )
        )

    def save_impact_target_mapping(self, value: ImpactTargetMapping) -> None:
        with self.transaction() as repository:
            repository.save_impact_target_mapping(value)

    def get_impact_target_mapping(self, mapping_id: str) -> Optional[ImpactTargetMapping]:
        return self._read(lambda repository: repository.get_impact_target_mapping(mapping_id))

    def update_impact_target_mapping(self, value: ImpactTargetMapping) -> None:
        with self.transaction() as repository:
            repository.update_impact_target_mapping(value)

    def list_impact_target_mappings(
        self, target_id: Optional[str] = None, status: Optional[str] = None
    ) -> list[ImpactTargetMapping]:
        return self._read(
            lambda repository: repository.list_impact_target_mappings(target_id, status)
        )

    def save_market_master_data_import_run(self, value: MarketMasterDataImportRun) -> None:
        with self.transaction() as repository:
            repository.save_market_master_data_import_run(value)

    def get_market_master_data_import_run(self, run_id: str) -> Optional[MarketMasterDataImportRun]:
        return self._read(lambda repository: repository.get_market_master_data_import_run(run_id))

    def find_market_master_data_import_run_by_hash(
        self, source_hash: str
    ) -> Optional[MarketMasterDataImportRun]:
        return self._read(
            lambda repository: repository.find_market_master_data_import_run_by_hash(source_hash)
        )

    def update_market_master_data_import_run(self, value: MarketMasterDataImportRun) -> None:
        with self.transaction() as repository:
            repository.update_market_master_data_import_run(value)

    def list_market_master_data_import_runs(self) -> list[MarketMasterDataImportRun]:
        return self._read(lambda repository: repository.list_market_master_data_import_runs())

    def save_event_impact_relation(self, relation: EventImpactRelation) -> None:
        with self.transaction() as repository:
            repository.save_event_impact_relation(relation)

    def list_event_impact_relations(
        self, event_id: Optional[str] = None
    ) -> list[EventImpactRelation]:
        return self._read(lambda repository: repository.list_event_impact_relations(event_id))

    def save_impact_contribution(self, contribution: ImpactContribution) -> None:
        with self.transaction() as repository:
            repository.save_impact_contribution(contribution)

    def list_impact_contributions(
        self, target_id: Optional[str] = None
    ) -> list[ImpactContribution]:
        return self._read(lambda repository: repository.list_impact_contributions(target_id))

    def save_target_impact_snapshot(
        self, snapshot: TargetImpactSnapshot, contributions: list[TargetImpactSnapshotContribution]
    ) -> None:
        with self.transaction() as repository:
            repository.save_target_impact_snapshot(snapshot, contributions)

    def get_latest_target_impact_snapshot(
        self,
        target_id: str,
        horizon: Optional[str] = None,
        scenario_set_id: str = "baseline",
        as_of: Optional[datetime] = None,
    ) -> Optional[TargetImpactSnapshot]:
        return self._read(
            lambda repository: repository.get_latest_target_impact_snapshot(
                target_id, horizon, scenario_set_id, as_of
            )
        )

    def list_target_impact_snapshot_contributions(
        self, snapshot_id: str
    ) -> list[TargetImpactSnapshotContribution]:
        return self._read(
            lambda repository: repository.list_target_impact_snapshot_contributions(snapshot_id)
        )

    def save_market_forecast_run(self, value: MarketForecastRun) -> None:
        with self.transaction() as repository:
            repository.save_market_forecast_run(value)

    def get_market_forecast_run(self, forecast_id: str) -> Optional[MarketForecastRun]:
        return self._read(lambda repository: repository.get_market_forecast_run(forecast_id))

    def find_market_forecast_run_by_source_hash(
        self, source_hash: str
    ) -> Optional[MarketForecastRun]:
        return self._read(
            lambda repository: repository.find_market_forecast_run_by_source_hash(source_hash)
        )

    def list_market_forecast_runs(
        self,
        instrument_id: Optional[str] = None,
        horizon: Optional[int] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 500,
    ) -> list[MarketForecastRun]:
        return self._read(
            lambda repository: repository.list_market_forecast_runs(
                instrument_id, horizon, start, end, limit
            )
        )

    def save_market_forecast_outcome(self, value: MarketForecastOutcome) -> None:
        with self.transaction() as repository:
            repository.save_market_forecast_outcome(value)

    def get_market_forecast_outcome(self, forecast_id: str) -> Optional[MarketForecastOutcome]:
        return self._read(lambda repository: repository.get_market_forecast_outcome(forecast_id))

    def list_market_forecast_outcomes(
        self, forecast_ids: Optional[list[str]] = None
    ) -> list[MarketForecastOutcome]:
        return self._read(lambda repository: repository.list_market_forecast_outcomes(forecast_ids))

    def save_market_calibration_version(self, value: MarketCalibrationVersion) -> None:
        with self.transaction() as repository:
            repository.save_market_calibration_version(value)

    def get_market_calibration_version(
        self, calibration_id: str
    ) -> Optional[MarketCalibrationVersion]:
        return self._read(
            lambda repository: repository.get_market_calibration_version(calibration_id)
        )

    def update_market_calibration_version(self, value: MarketCalibrationVersion) -> None:
        with self.transaction() as repository:
            repository.update_market_calibration_version(value)

    def list_market_calibration_versions(
        self,
        model_key: Optional[str] = None,
        market: Optional[str] = None,
        horizon: Optional[int] = None,
        status: Optional[str] = None,
    ) -> list[MarketCalibrationVersion]:
        return self._read(
            lambda repository: repository.list_market_calibration_versions(
                model_key, market, horizon, status
            )
        )

    def save_forward_impact_window(self, value: ForwardImpactWindow) -> None:
        with self.transaction() as repository:
            repository.save_forward_impact_window(value)

    def get_forward_impact_window(self, window_id: str) -> Optional[ForwardImpactWindow]:
        return self._read(lambda repository: repository.get_forward_impact_window(window_id))

    def save_forward_catalyst(self, value: ForwardCatalyst) -> None:
        with self.transaction() as repository:
            repository.save_forward_catalyst(value)

    def get_forward_catalyst(self, catalyst_id: str) -> Optional[ForwardCatalyst]:
        return self._read(lambda repository: repository.get_forward_catalyst(catalyst_id))

    def list_forward_catalysts(self, target_id: Optional[str] = None) -> list[ForwardCatalyst]:
        return self._read(lambda repository: repository.list_forward_catalysts(target_id))

    def save_forward_contribution(self, value: ForwardImpactContribution) -> None:
        with self.transaction() as repository:
            repository.save_forward_contribution(value)

    def list_forward_contributions(self, window_id: str) -> list[ForwardImpactContribution]:
        return self._read(lambda repository: repository.list_forward_contributions(window_id))

    def save_forward_points(self, values: list[ForwardImpactPoint]) -> None:
        with self.transaction() as repository:
            repository.save_forward_points(values)

    def list_forward_points(
        self, window_id: str, scenario_id: str = "baseline"
    ) -> list[ForwardImpactPoint]:
        return self._read(lambda repository: repository.list_forward_points(window_id, scenario_id))

    def save_future_event(self, value: FutureEvent) -> None:
        with self.transaction() as repository:
            repository.save_future_event(value)

    def get_future_event(self, event_id: str) -> Optional[FutureEvent]:
        return self._read(lambda repository: repository.get_future_event(event_id))

    def list_future_events(self) -> list[FutureEvent]:
        return self._read(lambda repository: repository.list_future_events())

    def save_future_event_revision(self, value: FutureEventRevision) -> None:
        with self.transaction() as repository:
            repository.save_future_event_revision(value)

    def get_future_event_revision(self, revision_id: str) -> Optional[FutureEventRevision]:
        return self._read(lambda repository: repository.get_future_event_revision(revision_id))

    def list_future_event_revisions(self, event_id: str) -> list[FutureEventRevision]:
        return self._read(lambda repository: repository.list_future_event_revisions(event_id))

    def save_future_event_target_impact(self, value: FutureEventTargetImpact) -> None:
        with self.transaction() as repository:
            repository.save_future_event_target_impact(value)

    def list_future_event_target_impacts(
        self, event_id: Optional[str] = None, target_id: Optional[str] = None
    ) -> list[FutureEventTargetImpact]:
        return self._read(
            lambda repository: repository.list_future_event_target_impacts(event_id, target_id)
        )

    # Agent Runtime (DD-80)
    def save_agent_registration(self, registration: AgentRegistration) -> None:
        with self.transaction() as repository:
            repository.save_agent_registration(registration)

    def get_agent_registration(self, agent_key: str) -> Optional[AgentRegistration]:
        return self._read(lambda repository: repository.get_agent_registration(agent_key))

    def list_agent_registrations(self) -> list[AgentRegistration]:
        return self._read(lambda repository: repository.list_agent_registrations())

    def save_research_plan(self, plan: ResearchPlan) -> None:
        with self.transaction() as repository:
            repository.save_research_plan(plan)

    def get_research_plan(self, plan_id: str) -> Optional[ResearchPlan]:
        return self._read(lambda repository: repository.get_research_plan(plan_id))

    def get_research_plan_by_workflow(self, workflow_id: str) -> Optional[ResearchPlan]:
        return self._read(lambda repository: repository.get_research_plan_by_workflow(workflow_id))

    def list_research_plans(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[ResearchPlan]:
        return self._read(lambda repository: repository.list_research_plans(status, limit, cursor))

    def update_research_plan(self, plan: ResearchPlan) -> None:
        with self.transaction() as repository:
            repository.update_research_plan(plan)

    def save_research_task(self, task: ResearchTask) -> None:
        with self.transaction() as repository:
            repository.save_research_task(task)

    def get_research_task(self, task_id: str) -> Optional[ResearchTask]:
        return self._read(lambda repository: repository.get_research_task(task_id))

    def list_research_tasks(self, plan_id: str) -> list[ResearchTask]:
        return self._read(lambda repository: repository.list_research_tasks(plan_id))

    def update_research_task(self, task: ResearchTask) -> None:
        with self.transaction() as repository:
            repository.update_research_task(task)

    def get_review_task(self, task_id: str) -> Optional[ReviewTask]:
        return self._read(lambda repository: repository.get_review_task(task_id))

    def list_review_tasks(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[ReviewTask]:
        return self._read(lambda repository: repository.list_review_tasks(status, limit, cursor))

    def update_review_task(self, task: ReviewTask) -> None:
        with self.transaction() as repository:
            repository.update_review_task(task)

    def save_model_run(self, run: ModelRun) -> None:
        with self.transaction() as repository:
            repository.save_model_run(run)

    def find_model_run_by_hash(self, request_hash: str) -> Optional[ModelRun]:
        return self._read(lambda repository: repository.find_model_run_by_hash(request_hash))

    def list_model_runs(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[ModelRun]:
        return self._read(lambda repository: repository.list_model_runs(limit, cursor))

    def save_workflow_run(self, run: WorkflowRun) -> None:
        with self.transaction() as repository:
            repository.save_workflow_run(run)

    def get_workflow_run(self, workflow_id: str) -> Optional[WorkflowRun]:
        return self._read(lambda repository: repository.get_workflow_run(workflow_id))

    def list_workflow_runs(
        self,
        event_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[WorkflowRun]:
        return self._read(
            lambda repository: repository.list_workflow_runs(event_id, status, limit, cursor)
        )

    def update_workflow_run(self, run: WorkflowRun) -> None:
        with self.transaction() as repository:
            repository.update_workflow_run(run)

    def get_fact_card_for_event(
        self, event_id: str, as_of: Optional[datetime] = None
    ) -> Optional[FactCard]:
        return self._read(lambda repository: repository.get_fact_card_for_event(event_id, as_of))

    def list_fact_cards_for_event(self, event_id: str) -> list[FactCard]:
        return self._read(lambda repository: repository.list_fact_cards_for_event(event_id))

    def list_fact_cards(
        self,
        event_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[FactCard]:
        return self._read(
            lambda repository: repository.list_fact_cards(event_id, status, limit, cursor)
        )

    def save_fact_card(self, fact_card: FactCard) -> None:
        with self.transaction() as repository:
            repository.save_fact_card(fact_card)

    def count_claims(self) -> int:
        return self._read(lambda repository: repository.count_claims())

    def count_claims_with_evidence(self) -> int:
        return self._read(lambda repository: repository.count_claims_with_evidence())

    def get_conflict(self, conflict_id: str) -> Optional[ConflictRecord]:
        return self._read(lambda repository: repository.get_conflict(conflict_id))

    def update_conflict(self, conflict: ConflictRecord) -> None:
        with self.transaction() as repository:
            repository.update_conflict(conflict)

    def get_merge_review_task(self, task_id: str) -> Optional[MergeReviewTask]:
        return self._read(lambda repository: repository.get_merge_review_task(task_id))

    def list_merge_review_tasks(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[MergeReviewTask]:
        return self._read(
            lambda repository: repository.list_merge_review_tasks(status, limit, cursor)
        )

    def update_merge_review_task(self, task: MergeReviewTask) -> None:
        with self.transaction() as repository:
            repository.update_merge_review_task(task)

    def save_watch_trigger(self, trigger: WatchTrigger) -> None:
        with self.transaction() as repository:
            repository.save_watch_trigger(trigger)

    def get_watch_trigger(self, trigger_id: str) -> Optional[WatchTrigger]:
        return self._read(lambda repository: repository.get_watch_trigger(trigger_id))

    def list_watch_triggers(
        self,
        status: Optional[str] = None,
        event_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[WatchTrigger]:
        return self._read(
            lambda repository: repository.list_watch_triggers(status, event_id, limit)
        )

    def update_watch_trigger(self, trigger: WatchTrigger) -> None:
        with self.transaction() as repository:
            repository.update_watch_trigger(trigger)

    def get_event_type_registry(self, type_label: str) -> Optional[EventTypeRegistryEntry]:
        return self._read(lambda repository: repository.get_event_type_registry(type_label))

    def list_event_type_registry(
        self, status: Optional[str] = None
    ) -> list[EventTypeRegistryEntry]:
        return self._read(lambda repository: repository.list_event_type_registry(status))

    def save_event_type_registry(self, entry: EventTypeRegistryEntry) -> None:
        with self.transaction() as repository:
            repository.save_event_type_registry(entry)

    def increment_event_type_registry_count(self, type_label: str) -> EventTypeRegistryEntry:
        with self.transaction() as repository:
            return repository.increment_event_type_registry_count(type_label)

    def get_api_idempotent(self, key: str) -> Optional[ApiIdempotencyRecord]:
        return self._read(lambda repository: repository.get_api_idempotent(key))

    def save_api_idempotent(self, key: str, value: ApiIdempotencyRecord) -> None:
        with self.transaction() as repository:
            repository.save_api_idempotent(key, value)

    def list_pending_outbox(
        self, limit: int, now: Optional[datetime] = None
    ) -> list[OutboxMessage]:
        return self._read(lambda repository: repository.list_pending_outbox(limit, now))

    def list_pending_outbox_by_event_type(
        self, event_type: str, limit: int, now: Optional[datetime] = None
    ) -> list[OutboxMessage]:
        return self._read(
            lambda repository: repository.list_pending_outbox_by_event_type(event_type, limit, now)
        )

    def mark_outbox_published(self, message_id: str, published_at: datetime) -> None:
        with self.transaction() as repository:
            repository.mark_outbox_published(message_id, published_at)

    def mark_outbox_failed(self, message_id: str, error: str, next_attempt_at: datetime) -> None:
        with self.transaction() as repository:
            repository.mark_outbox_failed(message_id, error, next_attempt_at)

    def mark_outbox_dead_lettered(
        self, message_id: str, error: str, dead_lettered_at: datetime
    ) -> None:
        with self.transaction() as repository:
            repository.mark_outbox_dead_lettered(message_id, error, dead_lettered_at)

    def list_outbox(
        self,
        dead_lettered: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[OutboxMessage]:
        return self._read(lambda repository: repository.list_outbox(dead_lettered, limit, cursor))

    def get_outbox(self, outbox_id: str) -> Optional[OutboxMessage]:
        return self._read(lambda repository: repository.get_outbox(outbox_id))

    def retry_outbox(self, outbox_id: str) -> None:
        with self.transaction() as repository:
            repository.retry_outbox(outbox_id)

    # --- 用户与审计（登录链路依赖）---
    def save_user(self, user: User) -> None:
        with self.transaction() as repository:
            repository.save_user(user)

    def get_user_by_username(self, username: str) -> Optional[User]:
        return self._read(lambda repository: repository.get_user_by_username(username))

    def get_user(self, user_id: str) -> Optional[User]:
        return self._read(lambda repository: repository.get_user(user_id))

    def list_users(self) -> list[User]:
        return self._read(lambda repository: repository.list_users())

    def update_user(self, user: User) -> None:
        with self.transaction() as repository:
            repository.update_user(user)

    def save_audit_log(self, log: AuditLog) -> None:
        with self.transaction() as repository:
            repository.save_audit_log(log)

    def list_audit_logs(
        self, limit: Optional[int] = 100, cursor: Optional[str] = None
    ) -> list[AuditLog]:
        return self._read(lambda repository: repository.list_audit_logs(limit, cursor))

    # --- 来源与隔离 ---
    def save_source(self, source: Source) -> None:
        with self.transaction() as repository:
            repository.save_source(source)

    def get_source(self, source_id: str) -> Optional[Source]:
        return self._read(lambda repository: repository.get_source(source_id))

    def get_source_by_code(self, code: str) -> Optional[Source]:
        return self._read(lambda repository: repository.get_source_by_code(code))

    def list_sources(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[Source]:
        return self._read(lambda repository: repository.list_sources(limit, cursor))

    def update_source(self, source: Source) -> None:
        with self.transaction() as repository:
            repository.update_source(source)

    def save_ingest_run(self, run: IngestRun) -> None:
        with self.transaction() as repository:
            repository.save_ingest_run(run)

    def update_ingest_run(self, run: IngestRun) -> None:
        with self.transaction() as repository:
            repository.update_ingest_run(run)

    def get_ingest_run(self, run_id: str) -> Optional[IngestRun]:
        return self._read(lambda repository: repository.get_ingest_run(run_id))

    def list_ingest_runs(
        self,
        source_id: str,
        limit: Optional[int] = 20,
        cursor: Optional[str] = None,
    ) -> list[IngestRun]:
        return self._read(lambda repository: repository.list_ingest_runs(source_id, limit, cursor))

    def save_llm_provider(self, config: LlmProviderConfig) -> None:
        with self.transaction() as repository:
            repository.save_llm_provider(config)

    def get_llm_provider(self, provider_id: str) -> Optional[LlmProviderConfig]:
        return self._read(lambda repository: repository.get_llm_provider(provider_id))

    def get_llm_provider_by_code(self, code: str) -> Optional[LlmProviderConfig]:
        return self._read(lambda repository: repository.get_llm_provider_by_code(code))

    def get_default_llm_provider(self) -> Optional[LlmProviderConfig]:
        return self._read(lambda repository: repository.get_default_llm_provider())

    def list_llm_providers(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[LlmProviderConfig]:
        return self._read(lambda repository: repository.list_llm_providers(limit, cursor))

    def update_llm_provider(self, config: LlmProviderConfig) -> None:
        with self.transaction() as repository:
            repository.update_llm_provider(config)

    def delete_llm_provider(self, provider_id: str) -> None:
        with self.transaction() as repository:
            repository.delete_llm_provider(provider_id)

    def upsert_llm_agent_binding(self, binding: LlmAgentBinding) -> None:
        with self.transaction() as repository:
            repository.upsert_llm_agent_binding(binding)

    def get_llm_agent_binding(self, agent_key: str) -> Optional[LlmAgentBinding]:
        return self._read(lambda repository: repository.get_llm_agent_binding(agent_key))

    def list_llm_agent_bindings(self) -> list[LlmAgentBinding]:
        return self._read(lambda repository: repository.list_llm_agent_bindings())

    def save_quarantine_item(self, item: QuarantineItem) -> None:
        with self.transaction() as repository:
            repository.save_quarantine_item(item)

    def list_quarantine_items(
        self,
        source_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[QuarantineItem]:
        return self._read(
            lambda repository: repository.list_quarantine_items(source_id, status, limit, cursor)
        )

    def get_quarantine_item(self, item_id: str) -> Optional[QuarantineItem]:
        return self._read(lambda repository: repository.get_quarantine_item(item_id))

    def update_quarantine_item(self, item: QuarantineItem) -> None:
        with self.transaction() as repository:
            repository.update_quarantine_item(item)

    # --- 工具调用 / 预算 / 节点幂等 ---
    def save_tool_call(self, call: ToolCall) -> None:
        with self.transaction() as repository:
            repository.save_tool_call(call)

    def list_tool_calls(self, workflow_id: str) -> list[ToolCall]:
        return self._read(lambda repository: repository.list_tool_calls(workflow_id))

    def save_budget_ledger(self, entry: BudgetLedgerEntry) -> None:
        with self.transaction() as repository:
            repository.save_budget_ledger(entry)

    def list_budget_ledger(self, workflow_id: str) -> list[BudgetLedgerEntry]:
        return self._read(lambda repository: repository.list_budget_ledger(workflow_id))

    def save_node_attempt(self, attempt: NodeAttempt) -> None:
        with self.transaction() as repository:
            repository.save_node_attempt(attempt)

    def find_node_attempt(
        self, workflow_id: str, node_name: str, input_hash: str
    ) -> Optional[NodeAttempt]:
        return self._read(
            lambda repository: repository.find_node_attempt(workflow_id, node_name, input_hash)
        )

    def list_node_attempts(
        self, workflow_id: str, node_name: Optional[str] = None
    ) -> list[NodeAttempt]:
        return self._read(lambda repository: repository.list_node_attempts(workflow_id, node_name))

    def invalidate_node_attempts(self, workflow_id: str, node_names: list[str]) -> int:
        with self.transaction() as repository:
            return repository.invalidate_node_attempts(workflow_id, node_names)

    # --- 报告与简报 ---
    def list_published_reports(self, start: datetime, end: datetime) -> list[FactCard]:
        return self._read(lambda repository: repository.list_published_reports(start, end))

    def save_brief(self, brief: Brief) -> None:
        with self.transaction() as repository:
            repository.save_brief(brief)

    def get_brief_by_date(self, brief_date: str) -> Optional[Brief]:
        return self._read(lambda repository: repository.get_brief_by_date(brief_date))

    # --- Document Intelligence ---
    def save_parsed_document(self, parsed: ParsedDocument) -> None:
        with self.transaction() as repository:
            repository.save_parsed_document(parsed)

    def get_parsed_document_by_document(self, document_id: str) -> Optional[ParsedDocument]:
        return self._read(
            lambda repository: repository.get_parsed_document_by_document(document_id)
        )

    def get_parsed_document_by_revision(self, revision_id: str) -> Optional[ParsedDocument]:
        return self._read(
            lambda repository: repository.get_parsed_document_by_revision(revision_id)
        )

    def save_document_block(self, block: DocumentBlock) -> None:
        with self.transaction() as repository:
            repository.save_document_block(block)

    def get_document_block(self, block_id: str) -> Optional[DocumentBlock]:
        return self._read(lambda repository: repository.get_document_block(block_id))

    def get_document_blocks_for_revision(self, revision_id: str) -> list[DocumentBlock]:
        return self._read(
            lambda repository: repository.get_document_blocks_for_revision(revision_id)
        )

    def save_document_chunk(self, chunk: DocumentChunk) -> None:
        with self.transaction() as repository:
            repository.save_document_chunk(chunk)

    def get_document_chunks_for_block(self, block_id: str) -> list[DocumentChunk]:
        return self._read(lambda repository: repository.get_document_chunks_for_block(block_id))

    def save_embedding_record(self, record: EmbeddingRecord) -> None:
        with self.transaction() as repository:
            repository.save_embedding_record(record)

    def get_embedding_record(self, record_id: str) -> Optional[EmbeddingRecord]:
        return self._read(lambda repository: repository.get_embedding_record(record_id))

    def find_embedding_record_by_chunk_and_model(
        self, chunk_id: str, model_version: str
    ) -> Optional[EmbeddingRecord]:
        return self._read(
            lambda repository: repository.find_embedding_record_by_chunk_and_model(
                chunk_id, model_version
            )
        )

    def list_embedding_records_by_chunks(self, chunk_ids: list[str]) -> list[EmbeddingRecord]:
        return self._read(lambda repository: repository.list_embedding_records_by_chunks(chunk_ids))

    def find_similar_document_chunks(
        self,
        query_embedding: list[float],
        model_version: str,
        top_k: int = 10,
        as_of: Optional[datetime] = None,
        chunk_types: Optional[list[str]] = None,
        source_tiers: Optional[list[str]] = None,
    ) -> list[tuple[DocumentChunk, float]]:
        return self._read(
            lambda repository: repository.find_similar_document_chunks(
                query_embedding,
                model_version,
                top_k=top_k,
                as_of=as_of,
                chunk_types=chunk_types,
                source_tiers=source_tiers,
            )
        )

    def find_document_chunks_by_keywords(
        self,
        keywords: list[str],
        top_k: int = 10,
        as_of: Optional[datetime] = None,
        chunk_types: Optional[list[str]] = None,
        source_tiers: Optional[list[str]] = None,
    ) -> list[tuple[DocumentChunk, float]]:
        return self._read(
            lambda repository: repository.find_document_chunks_by_keywords(
                keywords,
                top_k=top_k,
                as_of=as_of,
                chunk_types=chunk_types,
                source_tiers=source_tiers,
            )
        )

    def list_disclosure_groups_with_embeddings(self, model_version: str) -> list[DisclosureGroup]:
        return self._read(
            lambda repository: repository.list_disclosure_groups_with_embeddings(model_version)
        )

    def find_similar_disclosure_groups(
        self,
        query_embedding: list[float],
        model_version: str,
        top_k: int = 10,
    ) -> list[tuple[DisclosureGroup, float]]:
        return self._read(
            lambda repository: repository.find_similar_disclosure_groups(
                query_embedding, model_version, top_k
            )
        )

    def save_disclosure_group(self, group: DisclosureGroup) -> None:
        with self.transaction() as repository:
            repository.save_disclosure_group(group)

    def get_disclosure_group(self, group_id: str) -> Optional[DisclosureGroup]:
        return self._read(lambda repository: repository.get_disclosure_group(group_id))

    def find_disclosure_group_by_content_hash(
        self, canonical_content_hash: str
    ) -> Optional[DisclosureGroup]:
        return self._read(
            lambda repository: repository.find_disclosure_group_by_content_hash(
                canonical_content_hash
            )
        )

    def save_disclosure_group_membership(self, membership: DisclosureGroupMembership) -> None:
        with self.transaction() as repository:
            repository.save_disclosure_group_membership(membership)

    def list_disclosure_group_members(self, group_id: str) -> list[DisclosureGroupMembership]:
        return self._read(lambda repository: repository.list_disclosure_group_members(group_id))

    def get_disclosure_group_for_document(self, document_id: str) -> Optional[DisclosureGroup]:
        return self._read(
            lambda repository: repository.get_disclosure_group_for_document(document_id)
        )

    # --- 文档 / 证据（API 直读 Provider，必须代理；否则 GET /evidence 500）---
    def get_document(
        self, document_id: str, *, include_deleted: bool = False
    ) -> Optional[Document]:
        return self._read(
            lambda repository: repository.get_document(document_id, include_deleted=include_deleted)
        )

    def get_evidence(
        self, evidence_id: str, *, include_deleted: bool = False
    ) -> Optional[EvidenceSpan]:
        return self._read(
            lambda repository: repository.get_evidence(evidence_id, include_deleted=include_deleted)
        )

    def set_document_retention_hold(self, document_id: str, hold: bool) -> Document:
        with self.transaction() as repository:
            return repository.set_document_retention_hold(document_id, hold)

    def soft_delete_document(
        self, document_id: str, *, deleted_at: Optional[datetime] = None
    ) -> Document:
        with self.transaction() as repository:
            return repository.soft_delete_document(document_id, deleted_at=deleted_at)

    def purge_document(
        self,
        document_id: str,
        *,
        purged_at: Optional[datetime] = None,
        min_soft_delete_age_seconds: int = 0,
    ) -> Document:
        with self.transaction() as repository:
            return repository.purge_document(
                document_id,
                purged_at=purged_at,
                min_soft_delete_age_seconds=min_soft_delete_age_seconds,
            )

    def list_documents_eligible_for_purge(
        self,
        *,
        deleted_before: datetime,
        limit: int = 100,
    ) -> list[Document]:
        return self._read(
            lambda repository: repository.list_documents_eligible_for_purge(
                deleted_before=deleted_before, limit=limit
            )
        )


class SqlAlchemyTransaction:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_document(
        self, source_id: str, external_id: Optional[str], content_hash: str
    ) -> Optional[Document]:
        statement = select(DocumentModel).where(DocumentModel.source_id == source_id)
        if external_id:
            model = self.session.scalar(statement.where(DocumentModel.external_id == external_id))
            if model:
                return _document(model)
        model = self.session.scalar(statement.where(DocumentModel.content_hash == content_hash))
        return _document(model) if model else None

    def save_user(self, value: User) -> None:
        data = value.__dict__.copy()
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc)
        self.session.add(UserModel(**data))
        self.session.flush()

    def get_user_by_username(self, username: str) -> Optional[User]:
        model = self.session.scalar(select(UserModel).where(UserModel.username == username))
        return _user(model) if model else None

    def get_user(self, user_id: str) -> Optional[User]:
        model = self.session.scalar(select(UserModel).where(UserModel.id == user_id))
        return _user(model) if model else None

    def list_users(self) -> list[User]:
        models = self.session.scalars(
            select(UserModel).order_by(UserModel.created_at, UserModel.id)
        )
        return [_user(model) for model in models]

    def update_user(self, value: User) -> None:
        model = self.session.get(UserModel, value.id)
        if not model:
            raise KeyError(f"User not found: {value.id}")
        for field, field_value in value.__dict__.items():
            if field != "id":
                setattr(model, field, field_value)
        self.session.flush()

    def save_audit_log(self, value: AuditLog) -> None:
        data = value.__dict__.copy()
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc)
        self.session.add(AuditLogModel(**data))
        self.session.flush()

    def save_review_task(self, value: ReviewTask) -> None:
        data = value.__dict__.copy()
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc)
        self.session.add(ReviewTaskModel(**data))
        self.session.flush()

    def get_review_policy(self) -> ReviewPolicy:
        model = self.session.get(ReviewPolicyModel, "review_policy:default")
        if model is None:
            return ReviewPolicy()
        return ReviewPolicy(
            id=model.id,
            mode=model.mode,
            min_confidence=model.min_confidence,
            updated_by=model.updated_by,
            updated_at=model.updated_at,
        )

    def save_review_policy(self, value: ReviewPolicy) -> None:
        model = self.session.get(ReviewPolicyModel, value.id)
        data = value.__dict__.copy()
        data["updated_at"] = data.get("updated_at") or datetime.now(timezone.utc)
        if model is None:
            self.session.add(ReviewPolicyModel(**data))
        else:
            for field, field_value in data.items():
                if field != "id":
                    setattr(model, field, field_value)
        self.session.flush()

    def get_source_collection_config(self) -> SourceCollectionConfig:
        model = self.session.get(SourceCollectionConfigModel, "source_collection:default")
        if model is None:
            return SourceCollectionConfig()
        return SourceCollectionConfig(
            id=model.id,
            scheduler_enabled=model.scheduler_enabled,
            default_crawl_interval_seconds=model.default_crawl_interval_seconds,
            max_concurrent_runs=model.max_concurrent_runs,
            retry_limit=model.retry_limit,
            updated_by=model.updated_by,
            updated_at=model.updated_at,
        )

    def save_source_collection_config(self, value: SourceCollectionConfig) -> None:
        model = self.session.get(SourceCollectionConfigModel, value.id)
        data = value.__dict__.copy()
        data["updated_at"] = data.get("updated_at") or datetime.now(timezone.utc)
        if model is None:
            self.session.add(SourceCollectionConfigModel(**data))
        else:
            for field, field_value in data.items():
                if field != "id":
                    setattr(model, field, field_value)
        self.session.flush()

    def save_auto_review_attempt(self, value: AutoReviewAttempt) -> None:
        self.session.add(AutoReviewAttemptModel(**value.__dict__))
        self.session.flush()

    def list_auto_review_attempts(self, task_id: str, limit: int = 20) -> list[AutoReviewAttempt]:
        statement = (
            select(AutoReviewAttemptModel)
            .where(AutoReviewAttemptModel.task_id == task_id)
            .order_by(AutoReviewAttemptModel.created_at.desc())
            .limit(limit)
        )
        return [_auto_review_attempt(model) for model in self.session.scalars(statement)]

    def get_review_task(self, task_id: str) -> Optional[ReviewTask]:
        model = self.session.get(ReviewTaskModel, task_id)
        return _review_task(model) if model else None

    def list_review_tasks(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[ReviewTask]:
        statement = select(ReviewTaskModel).order_by(
            ReviewTaskModel.created_at.desc(), ReviewTaskModel.id.desc()
        )
        if status:
            statement = statement.where(ReviewTaskModel.status == status)
        if cursor:
            statement = statement.where(
                _cursor_filter(ReviewTaskModel.created_at, ReviewTaskModel.id, cursor)
            )
        if limit is not None:
            statement = statement.limit(limit)
        return [_review_task(model) for model in self.session.scalars(statement)]

    def update_review_task(self, value: ReviewTask) -> None:
        model = self.session.get(ReviewTaskModel, value.id)
        if not model:
            raise KeyError(f"Review task not found: {value.id}")
        for field, field_value in value.__dict__.items():
            if field != "id":
                setattr(model, field, field_value)
        self.session.flush()

    def save_model_run(self, value: ModelRun) -> None:
        payload = dict(value.__dict__)
        payload["estimated_cost_usd"] = Decimal(str(value.estimated_cost_usd))
        payload["created_at"] = value.created_at or datetime.now(timezone.utc)
        self.session.add(ModelRunModel(**payload))
        self.session.flush()

    def find_model_run_by_hash(self, request_hash: str) -> Optional[ModelRun]:
        model = self.session.scalar(
            select(ModelRunModel)
            .where(ModelRunModel.request_hash == request_hash)
            .order_by(ModelRunModel.created_at.desc())
        )
        return _model_run(model) if model else None

    def list_model_runs(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[ModelRun]:
        statement = select(ModelRunModel).order_by(
            ModelRunModel.created_at.desc(), ModelRunModel.id.desc()
        )
        if cursor:
            statement = statement.where(
                _cursor_filter(ModelRunModel.created_at, ModelRunModel.id, cursor)
            )
        if limit is not None:
            statement = statement.limit(limit)
        return [_model_run(model) for model in self.session.scalars(statement)]

    def save_workflow_run(self, value: WorkflowRun) -> None:
        data = dict(value.__dict__)
        data.setdefault("created_at", datetime.now(timezone.utc))
        self.session.add(WorkflowRunModel(**data))
        self.session.flush()

    def get_workflow_run(self, workflow_id: str) -> Optional[WorkflowRun]:
        model = self.session.get(WorkflowRunModel, workflow_id)
        return _workflow_run(model) if model else None

    def list_workflow_runs(
        self,
        event_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[WorkflowRun]:
        statement = select(WorkflowRunModel).order_by(
            WorkflowRunModel.created_at.desc(), WorkflowRunModel.id.desc()
        )
        if event_id:
            statement = statement.where(WorkflowRunModel.event_id == event_id)
        if status:
            statement = statement.where(WorkflowRunModel.status == status)
        if cursor:
            statement = statement.where(
                _cursor_filter(WorkflowRunModel.created_at, WorkflowRunModel.id, cursor)
            )
        if limit is not None:
            statement = statement.limit(limit)
        return [_workflow_run(model) for model in self.session.scalars(statement)]

    def update_workflow_run(self, value: WorkflowRun) -> None:
        model = self.session.get(WorkflowRunModel, value.id)
        if not model:
            raise KeyError(f"Workflow not found: {value.id}")
        for field, field_value in value.__dict__.items():
            if field != "id":
                setattr(model, field, field_value)
        self.session.flush()

    def save_tool_call(self, value: ToolCall) -> None:
        data = value.__dict__.copy()
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc)
        self.session.add(ToolCallModel(**data))
        self.session.flush()

    def list_tool_calls(self, workflow_id: str) -> list[ToolCall]:
        models = self.session.scalars(
            select(ToolCallModel)
            .where(ToolCallModel.workflow_id == workflow_id)
            .order_by(ToolCallModel.created_at.desc())
        )
        return [
            ToolCall(
                id=model.id,
                workflow_id=model.workflow_id,
                agent_type=model.agent_type,
                tool_name=model.tool_name,
                arguments=model.arguments if model.arguments else {},
                result=model.result,
                as_of=model.as_of,
                status=model.status,
                error_code=model.error_code,
                duration_ms=model.duration_ms,
                created_at=model.created_at,
            )
            for model in models
        ]

    def save_budget_ledger(self, value: BudgetLedgerEntry) -> None:
        self.session.add(BudgetLedgerModel(**value.__dict__))
        self.session.flush()

    def list_budget_ledger(self, workflow_id: str) -> list[BudgetLedgerEntry]:
        models = self.session.scalars(
            select(BudgetLedgerModel)
            .where(BudgetLedgerModel.workflow_id == workflow_id)
            .order_by(BudgetLedgerModel.created_at)
        )
        return [
            BudgetLedgerEntry(
                id=model.id,
                workflow_id=model.workflow_id,
                node_name=model.node_name,
                dimension=model.dimension,
                entry_type=model.entry_type,
                amount=model.amount,
                created_at=model.created_at,
            )
            for model in models
        ]

    def save_node_attempt(self, value: NodeAttempt) -> None:
        existing = self.session.get(NodeAttemptModel, value.id)
        if existing:
            for field, field_value in value.__dict__.items():
                if field != "id":
                    setattr(existing, field, field_value)
        else:
            self.session.add(NodeAttemptModel(**value.__dict__))
        self.session.flush()

    def find_node_attempt(
        self, workflow_id: str, node_name: str, input_hash: str
    ) -> Optional[NodeAttempt]:
        model = self.session.scalar(
            select(NodeAttemptModel).where(
                NodeAttemptModel.workflow_id == workflow_id,
                NodeAttemptModel.node_name == node_name,
                NodeAttemptModel.input_hash == input_hash,
                NodeAttemptModel.status == "succeeded",
            )
        )
        if not model:
            return None
        return NodeAttempt(
            id=model.id,
            workflow_id=model.workflow_id,
            node_name=model.node_name,
            attempt_no=model.attempt_no,
            input_hash=model.input_hash,
            status=model.status,
            output=model.output,
            error_code=model.error_code,
            started_at=model.started_at,
            ended_at=model.ended_at,
        )

    def list_node_attempts(
        self, workflow_id: str, node_name: Optional[str] = None
    ) -> list[NodeAttempt]:
        statement = select(NodeAttemptModel).where(NodeAttemptModel.workflow_id == workflow_id)
        if node_name is not None:
            statement = statement.where(NodeAttemptModel.node_name == node_name)
        return [
            NodeAttempt(
                id=model.id,
                workflow_id=model.workflow_id,
                node_name=model.node_name,
                attempt_no=model.attempt_no,
                input_hash=model.input_hash,
                status=model.status,
                output=model.output,
                error_code=model.error_code,
                started_at=model.started_at,
                ended_at=model.ended_at,
            )
            for model in self.session.scalars(statement)
        ]

    def invalidate_node_attempts(self, workflow_id: str, node_names: list[str]) -> int:
        if not node_names:
            return 0
        models = list(
            self.session.scalars(
                select(NodeAttemptModel).where(
                    NodeAttemptModel.workflow_id == workflow_id,
                    NodeAttemptModel.node_name.in_(node_names),
                    NodeAttemptModel.status == "succeeded",
                )
            )
        )
        for model in models:
            model.status = "invalidated"
        self.session.flush()
        return len(models)

    def list_audit_logs(
        self, limit: Optional[int] = 100, cursor: Optional[str] = None
    ) -> list[AuditLog]:
        statement = select(AuditLogModel).order_by(
            AuditLogModel.created_at.desc(), AuditLogModel.id.desc()
        )
        if cursor:
            statement = statement.where(
                _cursor_filter(AuditLogModel.created_at, AuditLogModel.id, cursor)
            )
        if limit is not None:
            statement = statement.limit(limit)
        return [_audit_log(model) for model in self.session.scalars(statement)]

    def save_source(self, value: Source) -> None:
        self.session.add(
            SourceModel(
                **value.__dict__, created_at=value.last_success_at or datetime.now(timezone.utc)
            )
        )
        self.session.flush()

    def get_source(self, source_id: str) -> Optional[Source]:
        model = self.session.get(SourceModel, source_id)
        return _source(model) if model else None

    def get_source_by_code(self, code: str) -> Optional[Source]:
        model = self.session.scalar(select(SourceModel).where(SourceModel.code == code))
        return _source(model) if model else None

    def list_sources(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[Source]:
        statement = select(SourceModel).order_by(SourceModel.id.desc())
        if cursor:
            _, cursor_id = decode_cursor(cursor)
            statement = statement.where(SourceModel.id < cursor_id)
        if limit is not None:
            statement = statement.limit(limit)
        return [_source(model) for model in self.session.scalars(statement)]

    def update_source(self, value: Source) -> None:
        model = self.session.get(SourceModel, value.id)
        if not model:
            raise KeyError(f"Source not found: {value.id}")
        for field, field_value in value.__dict__.items():
            if field != "id":
                setattr(model, field, field_value)
        self.session.flush()

    def save_ingest_run(self, value: IngestRun) -> None:
        self.session.add(IngestRunModel(**value.__dict__))
        self.session.flush()

    def update_ingest_run(self, value: IngestRun) -> None:
        model = self.session.get(IngestRunModel, value.id)
        if not model:
            raise KeyError(f"IngestRun not found: {value.id}")
        for field, field_value in value.__dict__.items():
            if field != "id":
                setattr(model, field, field_value)
        self.session.flush()

    def get_ingest_run(self, run_id: str) -> Optional[IngestRun]:
        model = self.session.get(IngestRunModel, run_id)
        return _ingest_run(model) if model else None

    def list_ingest_runs(
        self,
        source_id: str,
        limit: Optional[int] = 20,
        cursor: Optional[str] = None,
    ) -> list[IngestRun]:
        statement = (
            select(IngestRunModel)
            .where(IngestRunModel.source_id == source_id)
            .order_by(IngestRunModel.started_at.desc(), IngestRunModel.id.desc())
        )
        if cursor:
            statement = statement.where(
                _cursor_filter(IngestRunModel.started_at, IngestRunModel.id, cursor)
            )
        if limit is not None:
            statement = statement.limit(limit)
        return [_ingest_run(model) for model in self.session.scalars(statement)]

    def save_llm_provider(self, value: LlmProviderConfig) -> None:
        data = value.__dict__.copy()
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc)
        if not data.get("updated_at"):
            data["updated_at"] = data["created_at"]
        self.session.add(LlmProviderConfigModel(**data))
        self.session.flush()

    def get_llm_provider(self, provider_id: str) -> Optional[LlmProviderConfig]:
        model = self.session.get(LlmProviderConfigModel, provider_id)
        return _llm_provider(model) if model else None

    def get_llm_provider_by_code(self, code: str) -> Optional[LlmProviderConfig]:
        model = self.session.scalar(
            select(LlmProviderConfigModel).where(LlmProviderConfigModel.code == code)
        )
        return _llm_provider(model) if model else None

    def get_default_llm_provider(self) -> Optional[LlmProviderConfig]:
        model = self.session.scalar(
            select(LlmProviderConfigModel).where(
                LlmProviderConfigModel.is_default.is_(True),
                LlmProviderConfigModel.status == "active",
            )
        )
        return _llm_provider(model) if model else None

    def list_llm_providers(
        self, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> list[LlmProviderConfig]:
        statement = select(LlmProviderConfigModel).order_by(
            LlmProviderConfigModel.created_at.desc(),
            LlmProviderConfigModel.id.desc(),
        )
        if cursor:
            statement = statement.where(
                _cursor_filter(
                    LlmProviderConfigModel.created_at,
                    LlmProviderConfigModel.id,
                    cursor,
                )
            )
        if limit is not None:
            statement = statement.limit(limit)
        return [_llm_provider(model) for model in self.session.scalars(statement)]

    def update_llm_provider(self, value: LlmProviderConfig) -> None:
        model = self.session.get(LlmProviderConfigModel, value.id)
        if not model:
            raise KeyError(f"LLM provider not found: {value.id}")
        for field, field_value in value.__dict__.items():
            if field != "id":
                setattr(model, field, field_value)
        self.session.flush()

    def delete_llm_provider(self, provider_id: str) -> None:
        model = self.session.get(LlmProviderConfigModel, provider_id)
        if model:
            self.session.delete(model)
        for binding in self.session.scalars(
            select(LlmAgentBindingModel).where(LlmAgentBindingModel.provider_id == provider_id)
        ):
            binding.provider_id = None
        self.session.flush()

    def upsert_llm_agent_binding(self, value: LlmAgentBinding) -> None:
        model = self.session.get(LlmAgentBindingModel, value.agent_key)
        if model is None:
            data = value.__dict__.copy()
            if not data.get("updated_at"):
                data["updated_at"] = datetime.now(timezone.utc)
            self.session.add(LlmAgentBindingModel(**data))
        else:
            model.provider_id = value.provider_id
            model.model_override = value.model_override
            model.updated_at = value.updated_at or datetime.now(timezone.utc)
        self.session.flush()

    def get_llm_agent_binding(self, agent_key: str) -> Optional[LlmAgentBinding]:
        model = self.session.get(LlmAgentBindingModel, agent_key)
        return _llm_binding(model) if model else None

    def list_llm_agent_bindings(self) -> list[LlmAgentBinding]:
        return [
            _llm_binding(model)
            for model in self.session.scalars(
                select(LlmAgentBindingModel).order_by(LlmAgentBindingModel.agent_key)
            )
        ]

    def save_quarantine_item(self, value: QuarantineItem) -> None:
        self.session.add(QuarantineItemModel(**value.__dict__))
        self.session.flush()

    def list_quarantine_items(
        self,
        source_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[QuarantineItem]:
        statement = select(QuarantineItemModel).order_by(
            QuarantineItemModel.created_at.desc(), QuarantineItemModel.id.desc()
        )
        if source_id:
            statement = statement.where(QuarantineItemModel.source_id == source_id)
        if status:
            statement = statement.where(QuarantineItemModel.status == status)
        if cursor:
            statement = statement.where(
                _cursor_filter(QuarantineItemModel.created_at, QuarantineItemModel.id, cursor)
            )
        if limit is not None:
            statement = statement.limit(limit)
        return [_quarantine_item(model) for model in self.session.scalars(statement)]

    def get_quarantine_item(self, item_id: str) -> Optional[QuarantineItem]:
        model = self.session.get(QuarantineItemModel, item_id)
        return _quarantine_item(model) if model else None

    def update_quarantine_item(self, value: QuarantineItem) -> None:
        model = self.session.get(QuarantineItemModel, value.id)
        if not model:
            raise KeyError(f"Quarantine item not found: {value.id}")
        for field, field_value in value.__dict__.items():
            if field != "id":
                setattr(model, field, field_value)
        self.session.flush()

    def save_document(self, value: Document) -> None:
        self.session.add(DocumentModel(**value.__dict__, version=1))
        self.session.flush()

    def update_document(self, value: Document) -> None:
        model = self.session.get(DocumentModel, value.id)
        if not model:
            raise KeyError(f"Document not found: {value.id}")
        for field, field_value in value.__dict__.items():
            setattr(model, field, field_value)
        model.version += 1
        self.session.flush()

    def find_artifact(self, sha256: str) -> Optional[Artifact]:
        model = self.session.scalar(select(ArtifactModel).where(ArtifactModel.sha256 == sha256))
        return _artifact(model) if model else None

    def save_artifact(self, value: Artifact) -> None:
        self.session.add(ArtifactModel(**value.__dict__))
        self.session.flush()

    def save_document_revision(self, value: DocumentRevision) -> None:
        self.session.add(
            DocumentRevisionModel(
                id=value.id,
                document_id=value.document_id,
                revision_no=value.revision_no,
                artifact_id=value.artifact_id,
                content_hash=value.content_hash,
                normalized_content_uri=value.normalized_content_uri,
                parser_version=value.parser_version,
                metadata_json={},
                created_at=value.created_at,
            )
        )
        self.session.flush()

    def get_latest_revision(
        self, document_id: str, as_of: Optional[datetime] = None
    ) -> Optional[DocumentRevision]:
        statement = (
            select(DocumentRevisionModel)
            .where(DocumentRevisionModel.document_id == document_id)
            .order_by(DocumentRevisionModel.revision_no.desc())
        )
        if as_of is not None:
            statement = statement.where(DocumentRevisionModel.created_at <= as_of)
        model = self.session.scalar(statement)
        return _revision(model) if model else None

    def save_parsed_document(self, value: ParsedDocument) -> None:
        data = value.__dict__.copy()
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc)
        self.session.add(ParsedDocumentModel(**data))
        self.session.flush()

    def get_parsed_document_by_document(self, document_id: str) -> Optional[ParsedDocument]:
        model = self.session.scalar(
            select(ParsedDocumentModel)
            .where(ParsedDocumentModel.document_id == document_id)
            .order_by(ParsedDocumentModel.created_at.desc())
        )
        return _parsed_document(model) if model else None

    def get_parsed_document_by_revision(self, revision_id: str) -> Optional[ParsedDocument]:
        model = self.session.scalar(
            select(ParsedDocumentModel)
            .where(ParsedDocumentModel.revision_id == revision_id)
            .order_by(ParsedDocumentModel.created_at.desc())
        )
        return _parsed_document(model) if model else None

    def save_document_block(self, value: DocumentBlock) -> None:
        data = value.__dict__.copy()
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc)
        data["metadata_json"] = data.pop("metadata", {})
        self.session.add(DocumentBlockModel(**data))
        self.session.flush()

    def get_document_block(self, block_id: str) -> Optional[DocumentBlock]:
        model = self.session.get(DocumentBlockModel, block_id)
        return _document_block(model) if model else None

    def get_document_blocks_for_revision(self, revision_id: str) -> list[DocumentBlock]:
        models = self.session.scalars(
            select(DocumentBlockModel)
            .where(DocumentBlockModel.revision_id == revision_id)
            .order_by(DocumentBlockModel.order_index.asc())
        )
        return [_document_block(model) for model in models]

    def save_document_chunk(self, value: DocumentChunk) -> None:
        data = value.__dict__.copy()
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc)
        self.session.add(DocumentChunkModel(**data))
        self.session.flush()

    def get_document_chunks_for_block(self, block_id: str) -> list[DocumentChunk]:
        models = self.session.scalars(
            select(DocumentChunkModel)
            .where(DocumentChunkModel.block_id == block_id)
            .order_by(DocumentChunkModel.char_start.asc())
        )
        return [_document_chunk(model) for model in models]

    def save_embedding_record(self, value: EmbeddingRecord) -> None:
        data = value.__dict__.copy()
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc)
        self.session.add(EmbeddingRecordModel(**data))
        self.session.flush()

    def get_embedding_record(self, record_id: str) -> Optional[EmbeddingRecord]:
        model = self.session.get(EmbeddingRecordModel, record_id)
        return _embedding_record(model) if model else None

    def find_embedding_record_by_chunk_and_model(
        self, chunk_id: str, model_version: str
    ) -> Optional[EmbeddingRecord]:
        model = self.session.scalar(
            select(EmbeddingRecordModel)
            .where(EmbeddingRecordModel.chunk_id == chunk_id)
            .where(EmbeddingRecordModel.embedding_model_version == model_version)
        )
        return _embedding_record(model) if model else None

    def list_embedding_records_by_chunks(self, chunk_ids: list[str]) -> list[EmbeddingRecord]:
        if not chunk_ids:
            return []
        models = self.session.scalars(
            select(EmbeddingRecordModel).where(EmbeddingRecordModel.chunk_id.in_(chunk_ids))
        )
        return [_embedding_record(model) for model in models]

    def find_similar_document_chunks(
        self,
        query_embedding: list[float],
        model_version: str,
        top_k: int = 10,
        as_of: Optional[datetime] = None,
        chunk_types: Optional[list[str]] = None,
        source_tiers: Optional[list[str]] = None,
    ) -> list[tuple[DocumentChunk, float]]:
        # SQLite 测试路径没有 pgvector，回退到应用层 brute-force。
        if self.session.bind.dialect.name == "sqlite":
            from app.document_intelligence.embeddings import cosine_similarity

            scored: list[tuple[DocumentChunk, float]] = []
            models = self.session.scalars(
                select(EmbeddingRecordModel)
                .where(EmbeddingRecordModel.embedding_model_version == model_version)
                .where(EmbeddingRecordModel.status == "completed")
            )
            for record_model in models:
                chunk_model = self.session.get(DocumentChunkModel, record_model.chunk_id)
                if chunk_model is None:
                    continue
                if (
                    as_of is not None
                    and chunk_model.as_of is not None
                    and chunk_model.as_of > as_of
                ):
                    continue
                if chunk_types is not None and chunk_model.chunk_type not in chunk_types:
                    continue
                block_model = self.session.get(DocumentBlockModel, chunk_model.block_id)
                if block_model is None:
                    continue
                parsed_model = self.session.get(ParsedDocumentModel, block_model.parsed_document_id)
                if parsed_model is None:
                    continue
                document_model = self.session.get(DocumentModel, parsed_model.document_id)
                if document_model is None:
                    continue
                if source_tiers is not None and document_model.source_tier not in source_tiers:
                    continue
                score = cosine_similarity(query_embedding, record_model.embedding)
                scored.append((_document_chunk(chunk_model), score))
            scored.sort(key=lambda item: item[1], reverse=True)
            return scored[:top_k]

        # PostgreSQL + pgvector 路径：通过 JOIN 一次性过滤并排序。
        distance_expr = EmbeddingRecordModel.embedding.cosine_distance(query_embedding).label(
            "distance"
        )
        stmt = (
            select(DocumentChunkModel, distance_expr)
            .join(EmbeddingRecordModel, EmbeddingRecordModel.chunk_id == DocumentChunkModel.id)
            .join(DocumentBlockModel, DocumentChunkModel.block_id == DocumentBlockModel.id)
            .join(
                ParsedDocumentModel,
                DocumentBlockModel.parsed_document_id == ParsedDocumentModel.id,
            )
            .join(DocumentModel, ParsedDocumentModel.document_id == DocumentModel.id)
            .where(EmbeddingRecordModel.embedding_model_version == model_version)
            .where(EmbeddingRecordModel.status == "completed")
            .order_by(distance_expr.asc())
            .limit(top_k)
        )
        if as_of is not None:
            stmt = stmt.where(
                (DocumentChunkModel.as_of.is_(None)) | (DocumentChunkModel.as_of <= as_of)
            )
        if chunk_types:
            stmt = stmt.where(DocumentChunkModel.chunk_type.in_(chunk_types))
        if source_tiers:
            stmt = stmt.where(DocumentModel.source_tier.in_(source_tiers))

        rows = self.session.execute(stmt)
        return [(_document_chunk(model), round(1.0 - distance, 6)) for model, distance in rows]

    def find_document_chunks_by_keywords(
        self,
        keywords: list[str],
        top_k: int = 10,
        as_of: Optional[datetime] = None,
        chunk_types: Optional[list[str]] = None,
        source_tiers: Optional[list[str]] = None,
    ) -> list[tuple[DocumentChunk, float]]:
        # SQLite 测试路径没有 PostgreSQL full-text，回退到应用层 brute-force。
        if self.session.bind.dialect.name == "sqlite":
            from app.retrieval.lexical import score_chunk_text

            scored: list[tuple[DocumentChunk, float]] = []
            if not keywords:
                return scored

            chunk_type_set = set(chunk_types) if chunk_types else None
            source_tier_set = set(source_tiers) if source_tiers else None

            for chunk_model in self.session.scalars(select(DocumentChunkModel)):
                if (
                    as_of is not None
                    and chunk_model.as_of is not None
                    and chunk_model.as_of > as_of
                ):
                    continue
                if chunk_type_set is not None and chunk_model.chunk_type not in chunk_type_set:
                    continue
                block_model = self.session.get(DocumentBlockModel, chunk_model.block_id)
                if block_model is None:
                    continue
                parsed_model = self.session.get(ParsedDocumentModel, block_model.parsed_document_id)
                if parsed_model is None:
                    continue
                document_model = self.session.get(DocumentModel, parsed_model.document_id)
                if document_model is None:
                    continue
                if (
                    source_tier_set is not None
                    and document_model.source_tier not in source_tier_set
                ):
                    continue
                score = score_chunk_text(chunk_model.text, keywords)
                if score > 0:
                    scored.append((_document_chunk(chunk_model), score))
            scored.sort(key=lambda item: item[1], reverse=True)
            return scored[:top_k]

        from app.retrieval.lexical import build_tsquery

        tsquery_str = build_tsquery(keywords)
        if not tsquery_str:
            return []

        tsvector_expr = func.to_tsvector("simple", DocumentChunkModel.text)
        tsquery_expr = func.to_tsquery("simple", tsquery_str)
        rank_expr = func.ts_rank_cd(tsvector_expr, tsquery_expr).label("rank")

        stmt = (
            select(DocumentChunkModel, rank_expr)
            .join(DocumentBlockModel, DocumentChunkModel.block_id == DocumentBlockModel.id)
            .join(
                ParsedDocumentModel,
                DocumentBlockModel.parsed_document_id == ParsedDocumentModel.id,
            )
            .join(DocumentModel, ParsedDocumentModel.document_id == DocumentModel.id)
            .where(tsvector_expr.op("@@")(tsquery_expr))
            .order_by(rank_expr.desc())
            .limit(top_k)
        )
        if as_of is not None:
            stmt = stmt.where(
                (DocumentChunkModel.as_of.is_(None)) | (DocumentChunkModel.as_of <= as_of)
            )
        if chunk_types:
            stmt = stmt.where(DocumentChunkModel.chunk_type.in_(chunk_types))
        if source_tiers:
            stmt = stmt.where(DocumentModel.source_tier.in_(source_tiers))

        rows = self.session.execute(stmt)
        return [(_document_chunk(model), float(rank)) for model, rank in rows]

    def list_disclosure_groups_with_embeddings(self, model_version: str) -> list[DisclosureGroup]:
        models = self.session.scalars(
            select(DisclosureGroupModel)
            .where(DisclosureGroupModel.embedding_model_version == model_version)
            .where(DisclosureGroupModel.representative_embedding.isnot(None))
        )
        return [_disclosure_group(model) for model in models]

    def find_similar_disclosure_groups(
        self,
        query_embedding: list[float],
        model_version: str,
        top_k: int = 10,
    ) -> list[tuple[DisclosureGroup, float]]:
        # SQLite 测试路径没有 pgvector 扩展，回退到应用层 brute-force。
        if self.session.bind.dialect.name == "sqlite":
            from app.document_intelligence.embeddings import cosine_similarity

            scored: list[tuple[DisclosureGroup, float]] = []
            models = self.session.scalars(
                select(DisclosureGroupModel)
                .where(DisclosureGroupModel.embedding_model_version == model_version)
                .where(DisclosureGroupModel.representative_embedding.isnot(None))
            )
            for model in models:
                if model.representative_embedding is None:
                    continue
                score = cosine_similarity(query_embedding, model.representative_embedding)
                scored.append((_disclosure_group(model), score))
            scored.sort(key=lambda item: item[1], reverse=True)
            return scored[:top_k]

        # pgvector cosine_distance 返回 1 - cosine_similarity。
        distance_expr = DisclosureGroupModel.representative_embedding.cosine_distance(
            query_embedding
        ).label("distance")
        rows = self.session.execute(
            select(DisclosureGroupModel, distance_expr)
            .where(DisclosureGroupModel.embedding_model_version == model_version)
            .where(DisclosureGroupModel.representative_embedding.isnot(None))
            .order_by(distance_expr.asc())
            .limit(top_k)
        )
        return [(_disclosure_group(model), round(1.0 - distance, 6)) for model, distance in rows]

    def save_disclosure_group(self, value: DisclosureGroup) -> None:
        data = value.__dict__.copy()
        now = datetime.now(timezone.utc)
        if not data.get("created_at"):
            data["created_at"] = now
        if not data.get("updated_at"):
            data["updated_at"] = now
        self.session.add(DisclosureGroupModel(**data))
        self.session.flush()

    def get_disclosure_group(self, group_id: str) -> Optional[DisclosureGroup]:
        model = self.session.get(DisclosureGroupModel, group_id)
        return _disclosure_group(model) if model else None

    def find_disclosure_group_by_content_hash(
        self, canonical_content_hash: str
    ) -> Optional[DisclosureGroup]:
        model = self.session.scalar(
            select(DisclosureGroupModel).where(
                DisclosureGroupModel.canonical_content_hash == canonical_content_hash
            )
        )
        return _disclosure_group(model) if model else None

    def save_disclosure_group_membership(self, value: DisclosureGroupMembership) -> None:
        data = value.__dict__.copy()
        if not data.get("joined_at"):
            data["joined_at"] = datetime.now(timezone.utc)
        self.session.add(DisclosureGroupMembershipModel(**data))
        self.session.flush()

    def list_disclosure_group_members(self, group_id: str) -> list[DisclosureGroupMembership]:
        models = self.session.scalars(
            select(DisclosureGroupMembershipModel)
            .where(DisclosureGroupMembershipModel.disclosure_group_id == group_id)
            .order_by(DisclosureGroupMembershipModel.joined_at.desc())
        )
        return [_disclosure_group_membership(model) for model in models]

    def get_disclosure_group_for_document(self, document_id: str) -> Optional[DisclosureGroup]:
        model = self.session.scalar(
            select(DisclosureGroupMembershipModel)
            .where(DisclosureGroupMembershipModel.document_id == document_id)
            .order_by(DisclosureGroupMembershipModel.joined_at.desc())
        )
        if model is None:
            return None
        group_model = self.session.get(DisclosureGroupModel, model.disclosure_group_id)
        return _disclosure_group(group_model) if group_model else None

    def save_event(self, value: Event) -> None:
        data = value.__dict__.copy()
        data.pop("entity_links", None)
        data["importance"] = Decimal(str(value.importance))
        data["confidence"] = Decimal(str(value.confidence))
        self.session.add(EventModel(**data))
        self.session.flush()

    def update_event(self, value: Event) -> None:
        model = self.session.get(EventModel, value.id)
        if not model:
            raise KeyError(f"Event not found: {value.id}")
        model.status = value.status
        model.title = value.title
        model.entity_ids = value.entity_ids
        model.document_ids = value.document_ids
        model.disclosure_group_id = value.disclosure_group_id
        model.importance = Decimal(str(value.importance))
        model.urgency = value.urgency
        model.occurred_at = value.occurred_at
        model.key_fields = value.key_fields
        model.confidence = Decimal(str(value.confidence))
        model.classifier_version = value.classifier_version
        model.missing_required = value.missing_required
        model.time_resolution = value.time_resolution
        model.capability_pack_id = value.capability_pack_id
        model.capability_pack_version = value.capability_pack_version
        model.version = value.version
        self.session.flush()

    def save_entity(self, value: Entity) -> None:
        self.session.add(EntityModel(**value.__dict__))
        self.session.flush()

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        model = self.session.get(EntityModel, entity_id)
        return _entity(model) if model else None

    def save_security(self, value: Security) -> None:
        self.session.add(SecurityModel(**value.__dict__))
        self.session.flush()

    def get_security_by_market_code(self, market_code: str) -> Optional[Security]:
        model = self.session.scalar(
            select(SecurityModel).where(SecurityModel.market_code == market_code)
        )
        return _security(model) if model else None

    def save_event_entities(self, event_id: str, links: list[EntityLink]) -> None:
        for link in links:
            existing = self.session.scalar(
                select(EventEntityModel).where(
                    EventEntityModel.event_id == event_id,
                    EventEntityModel.entity_id == link.entity_id,
                )
            )
            if existing:
                continue
            self.session.add(
                EventEntityModel(
                    id=new_id("eve"),
                    event_id=event_id,
                    entity_id=link.entity_id,
                    market_code=link.market_code,
                    role=link.role,
                    confidence=Decimal(str(link.confidence)),
                    resolution_method=link.resolution_method,
                )
            )
        self.session.flush()

    def list_event_entities(self, event_id: str) -> list[EntityLink]:
        models = self.session.scalars(
            select(EventEntityModel).where(EventEntityModel.event_id == event_id)
        )
        return [
            EntityLink(
                entity_id=model.entity_id,
                market_code=model.market_code or "",
                role=model.role,
                confidence=float(model.confidence),
                resolution_method=model.resolution_method,
            )
            for model in models
        ]

    def save_merge_review_task(self, value: MergeReviewTask) -> None:
        self.session.add(MergeReviewTaskModel(**value.__dict__))
        self.session.flush()

    def get_merge_review_task(self, task_id: str) -> Optional[MergeReviewTask]:
        model = self.session.get(MergeReviewTaskModel, task_id)
        return _merge_review_task(model) if model else None

    def list_merge_review_tasks(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[MergeReviewTask]:
        statement = select(MergeReviewTaskModel).order_by(
            MergeReviewTaskModel.created_at, MergeReviewTaskModel.id
        )
        if status:
            statement = statement.where(MergeReviewTaskModel.status == status)
        if cursor:
            created_at, task_id = decode_cursor(cursor)
            statement = statement.where(
                (MergeReviewTaskModel.created_at > created_at)
                | (
                    (MergeReviewTaskModel.created_at == created_at)
                    & (MergeReviewTaskModel.id > task_id)
                )
            )
        if limit:
            statement = statement.limit(limit + 1)
        models = self.session.scalars(statement)
        return [_merge_review_task(model) for model in models]

    def update_merge_review_task(self, value: MergeReviewTask) -> None:
        model = self.session.get(MergeReviewTaskModel, value.id)
        if not model:
            raise KeyError(f"MergeReviewTask not found: {value.id}")
        model.status = value.status
        model.decision = value.decision
        model.reviewer_id = value.reviewer_id
        model.decided_at = value.decided_at
        self.session.flush()

    def save_watch_trigger(self, value: WatchTrigger) -> None:
        self.session.add(
            WatchTriggerModel(
                id=value.id,
                event_id=value.event_id,
                trigger_type=value.trigger_type,
                condition=value.condition,
                status=value.status,
                created_at=value.created_at or datetime.now(timezone.utc),
                fired_at=value.fired_at,
            )
        )
        self.session.flush()

    def get_watch_trigger(self, trigger_id: str) -> Optional[WatchTrigger]:
        model = self.session.get(WatchTriggerModel, trigger_id)
        return _watch_trigger(model) if model else None

    def list_watch_triggers(
        self,
        status: Optional[str] = None,
        event_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[WatchTrigger]:
        statement = select(WatchTriggerModel).order_by(
            WatchTriggerModel.created_at, WatchTriggerModel.id
        )
        if status:
            statement = statement.where(WatchTriggerModel.status == status)
        if event_id:
            statement = statement.where(WatchTriggerModel.event_id == event_id)
        if limit:
            statement = statement.limit(limit)
        models = self.session.scalars(statement)
        return [_watch_trigger(model) for model in models]

    def update_watch_trigger(self, value: WatchTrigger) -> None:
        model = self.session.get(WatchTriggerModel, value.id)
        if not model:
            raise KeyError(f"WatchTrigger not found: {value.id}")
        model.status = value.status
        model.condition = value.condition
        model.fired_at = value.fired_at
        self.session.flush()

    def get_event_type_registry(self, type_label: str) -> Optional[EventTypeRegistryEntry]:
        model = self.session.get(EventTypeRegistryModel, type_label)
        return _event_type_registry(model) if model else None

    def list_event_type_registry(
        self, status: Optional[str] = None
    ) -> list[EventTypeRegistryEntry]:
        statement = select(EventTypeRegistryModel).order_by(
            EventTypeRegistryModel.event_count.desc(),
            EventTypeRegistryModel.type_label,
        )
        if status:
            statement = statement.where(EventTypeRegistryModel.status == status)
        models = self.session.scalars(statement)
        return [_event_type_registry(model) for model in models]

    def save_event_type_registry(self, value: EventTypeRegistryEntry) -> None:
        now = datetime.now(timezone.utc)
        model = self.session.get(EventTypeRegistryModel, value.type_label)
        if model is None:
            self.session.add(
                EventTypeRegistryModel(
                    type_label=value.type_label,
                    status=value.status,
                    event_count=value.event_count,
                    decided_by=value.decided_by,
                    decided_at=value.decided_at,
                    created_at=value.created_at or now,
                    updated_at=value.updated_at or now,
                )
            )
        else:
            model.status = value.status
            model.event_count = value.event_count
            model.decided_by = value.decided_by
            model.decided_at = value.decided_at
            model.updated_at = value.updated_at or now
        self.session.flush()

    def increment_event_type_registry_count(self, type_label: str) -> EventTypeRegistryEntry:
        now = datetime.now(timezone.utc)
        model = self.session.get(EventTypeRegistryModel, type_label)
        if model is None:
            model = EventTypeRegistryModel(
                type_label=type_label,
                status="candidate",
                event_count=1,
                created_at=now,
                updated_at=now,
            )
            self.session.add(model)
        else:
            model.event_count += 1
            model.updated_at = now
        self.session.flush()
        return _event_type_registry(model)

    def save_ood_observation(self, value: OODObservation) -> None:
        model = self.session.get(OODObservationModel, value.id)
        fields = {
            "event_id": value.event_id,
            "document_id": value.document_id,
            "status": value.status,
            "ood_score": Decimal(str(value.ood_score)),
            "financial_relevance": Decimal(str(value.financial_relevance)),
            "closest_known_types": value.closest_known_types,
            "extracted_features": value.extracted_features,
            "evidence_ids": value.evidence_ids,
            "classifier_version": value.classifier_version,
            "router_version": value.router_version,
            "embedding_model_version": value.embedding_model_version,
            "generic_pack_id": value.generic_pack_id,
            "generic_pack_version": value.generic_pack_version,
            "cluster_id": value.cluster_id,
            "observed_at": value.observed_at or datetime.now(timezone.utc),
            "as_of": value.as_of,
            "version": value.version,
        }
        if model is None:
            self.session.add(OODObservationModel(id=value.id, **fields))
        else:
            for key, field_value in fields.items():
                setattr(model, key, field_value)
        self.session.flush()

    def get_ood_observation(self, observation_id: str) -> Optional[OODObservation]:
        model = self.session.get(OODObservationModel, observation_id)
        return _ood_observation(model) if model else None

    def list_ood_observations(
        self, status: Optional[str] = None, limit: Optional[int] = None
    ) -> list[OODObservation]:
        statement = select(OODObservationModel).order_by(
            OODObservationModel.observed_at.desc(), OODObservationModel.id.desc()
        )
        if status:
            statement = statement.where(OODObservationModel.status == status)
        if limit:
            statement = statement.limit(limit)
        return [_ood_observation(model) for model in self.session.scalars(statement)]

    def update_ood_observation(self, value: OODObservation) -> None:
        if self.session.get(OODObservationModel, value.id) is None:
            raise KeyError(f"OOD observation not found: {value.id}")
        self.save_ood_observation(value)

    def save_ood_cluster(self, value: OODCluster) -> None:
        model = self.session.get(OODClusterModel, value.id)
        fields = {
            "label": value.label,
            "status": value.status,
            "member_count": value.member_count,
            "independent_source_count": value.independent_source_count,
            "cohesion_score": Decimal(str(value.cohesion_score)),
            "separation_score": Decimal(str(value.separation_score)),
            "stability_score": Decimal(str(value.stability_score)),
            "first_seen_at": value.first_seen_at,
            "last_seen_at": value.last_seen_at,
            "cluster_version": value.cluster_version,
        }
        if model is None:
            self.session.add(OODClusterModel(id=value.id, **fields))
        else:
            for key, field_value in fields.items():
                setattr(model, key, field_value)
        self.session.flush()

    def get_ood_cluster(self, cluster_id: str) -> Optional[OODCluster]:
        model = self.session.get(OODClusterModel, cluster_id)
        return _ood_cluster(model) if model else None

    def list_ood_clusters(self, status: Optional[str] = None) -> list[OODCluster]:
        statement = select(OODClusterModel).order_by(OODClusterModel.id)
        if status:
            statement = statement.where(OODClusterModel.status == status)
        return [_ood_cluster(model) for model in self.session.scalars(statement)]

    def save_ood_feature_snapshot(self, value: OODFeatureSnapshot) -> None:
        model = self.session.get(OODFeatureSnapshotModel, value.id)
        if model is None:
            self.session.add(
                OODFeatureSnapshotModel(
                    id=value.id,
                    observation_id=value.observation_id,
                    feature_schema_version=value.feature_schema_version,
                    features=value.features,
                    generated_at=value.generated_at or datetime.now(timezone.utc),
                )
            )
        else:
            model.features = value.features
            model.feature_schema_version = value.feature_schema_version
        self.session.flush()

    def get_ood_feature_snapshot(self, snapshot_id: str) -> Optional[OODFeatureSnapshot]:
        model = self.session.get(OODFeatureSnapshotModel, snapshot_id)
        return _ood_feature_snapshot(model) if model else None

    def save_event_type_proposal(self, value: EventTypeProposal) -> None:
        model = self.session.get(EventTypeProposalModel, value.id)
        fields = {
            "cluster_id": value.cluster_id,
            "proposed_label": value.proposed_label,
            "display_name": value.display_name,
            "definition": value.definition,
            "status": value.status,
            "parent_type": value.parent_type,
            "inclusion_rules": value.inclusion_rules,
            "exclusion_rules": value.exclusion_rules,
            "required_fields": value.required_fields,
            "optional_fields": value.optional_fields,
            "mechanisms": value.mechanisms,
            "representative_event_ids": value.representative_event_ids,
            "counterexample_event_ids": value.counterexample_event_ids,
            "confidence": Decimal(str(value.confidence)),
            "agent_run_id": value.agent_run_id,
            "created_at": value.created_at or datetime.now(timezone.utc),
            "decided_at": value.decided_at,
        }
        if model is None:
            self.session.add(EventTypeProposalModel(id=value.id, **fields))
        else:
            for key, field_value in fields.items():
                setattr(model, key, field_value)
        self.session.flush()

    def get_event_type_proposal(self, proposal_id: str) -> Optional[EventTypeProposal]:
        model = self.session.get(EventTypeProposalModel, proposal_id)
        return _event_type_proposal(model) if model else None

    def list_event_type_proposals(self, status: Optional[str] = None) -> list[EventTypeProposal]:
        statement = select(EventTypeProposalModel).order_by(
            EventTypeProposalModel.created_at.desc()
        )
        if status:
            statement = statement.where(EventTypeProposalModel.status == status)
        return [_event_type_proposal(model) for model in self.session.scalars(statement)]

    def update_event_type_proposal(self, proposal: EventTypeProposal) -> None:
        if self.session.get(EventTypeProposalModel, proposal.id) is None:
            raise KeyError(f"event type proposal not found: {proposal.id}")
        self.save_event_type_proposal(proposal)

    def save_capability_evaluation(self, value: CapabilityEvaluation) -> None:
        model = self.session.get(CapabilityEvaluationModel, value.id)
        fields = {
            "pack_id": value.pack_id,
            "pack_version": value.pack_version,
            "baseline_pack_id": value.baseline_pack_id,
            "baseline_pack_version": value.baseline_pack_version,
            "status": value.status,
            "metrics": value.metrics,
            "comparison": value.comparison,
            "recommendation": value.recommendation,
            "created_at": value.created_at or datetime.now(timezone.utc),
        }
        if model is None:
            self.session.add(CapabilityEvaluationModel(id=value.id, **fields))
        else:
            for key, field_value in fields.items():
                setattr(model, key, field_value)
        self.session.flush()

    def get_capability_evaluation(self, evaluation_id: str) -> Optional[CapabilityEvaluation]:
        model = self.session.get(CapabilityEvaluationModel, evaluation_id)
        return _capability_evaluation(model) if model else None

    def list_capability_evaluations(
        self, pack_id: Optional[str] = None
    ) -> list[CapabilityEvaluation]:
        statement = select(CapabilityEvaluationModel).order_by(
            CapabilityEvaluationModel.created_at.desc()
        )
        if pack_id:
            statement = statement.where(CapabilityEvaluationModel.pack_id == pack_id)
        return [_capability_evaluation(model) for model in self.session.scalars(statement)]

    def save_reprocessing_job(self, value: ReprocessingJob) -> None:
        model = self.session.get(ReprocessingJobModel, value.id)
        fields = {
            "source_pack_id": value.source_pack_id,
            "target_pack_id": value.target_pack_id,
            "event_ids": value.event_ids,
            "status": value.status,
            "total_count": value.total_count,
            "success_count": value.success_count,
            "failed_count": value.failed_count,
            "summary": value.summary,
            "created_at": value.created_at or datetime.now(timezone.utc),
            "updated_at": value.updated_at or datetime.now(timezone.utc),
        }
        if model is None:
            self.session.add(ReprocessingJobModel(id=value.id, **fields))
        else:
            for key, field_value in fields.items():
                setattr(model, key, field_value)
        self.session.flush()

    def get_reprocessing_job(self, job_id: str) -> Optional[ReprocessingJob]:
        model = self.session.get(ReprocessingJobModel, job_id)
        return _reprocessing_job(model) if model else None

    def list_reprocessing_jobs(self) -> list[ReprocessingJob]:
        statement = select(ReprocessingJobModel).order_by(ReprocessingJobModel.created_at.desc())
        return [_reprocessing_job(model) for model in self.session.scalars(statement)]

    def update_reprocessing_job(self, job: ReprocessingJob) -> None:
        if self.session.get(ReprocessingJobModel, job.id) is None:
            raise KeyError(f"reprocessing job not found: {job.id}")
        self.save_reprocessing_job(job)

    def save_match_decision(self, value: MatchDecision) -> None:
        self.session.add(
            MatchDecisionModel(
                id=value.id,
                document_id=value.document_id,
                candidate_event_id=value.candidate_event_id,
                features=value.features,
                score=Decimal(str(value.score)),
                rule_version=value.rule_version,
                decision=value.decision,
                created_at=value.created_at or datetime.now(timezone.utc),
            )
        )
        self.session.flush()

    def list_match_decisions(self, document_id: str) -> list[MatchDecision]:
        models = self.session.scalars(
            select(MatchDecisionModel)
            .where(MatchDecisionModel.document_id == document_id)
            .order_by(MatchDecisionModel.created_at.desc())
        )
        return [
            MatchDecision(
                id=model.id,
                document_id=model.document_id,
                candidate_event_id=model.candidate_event_id,
                features=model.features if model.features else {},
                score=float(model.score),
                rule_version=model.rule_version,
                decision=model.decision,
                created_at=model.created_at,
            )
            for model in models
        ]

    def save_evidence(self, value: EvidenceSpan) -> None:
        self.session.add(EvidenceSpanModel(**value.__dict__))
        self.session.flush()

    def save_claim(self, value: Claim) -> None:
        data = value.__dict__.copy()
        data["confidence"] = Decimal(str(value.confidence))
        data.setdefault("subject_entity_id", None)
        data.setdefault("qualifiers", {})
        data.setdefault("fingerprint", "")
        data.setdefault("policy_version", "policy-v1")
        self.session.add(ClaimModel(**data, version=1))
        self.session.flush()

    def update_claim(self, value: Claim) -> None:
        from sqlalchemy import update

        self.session.execute(
            update(ClaimModel)
            .where(ClaimModel.id == value.id)
            .values(
                status=value.status,
                confidence=Decimal(str(value.confidence)),
                object_value=value.object_value,
                qualifiers=value.qualifiers,
                policy_version=value.policy_version or "policy-v1",
            )
        )
        self.session.flush()

    def count_claims(self) -> int:
        return self.session.scalar(select(func.count(ClaimModel.id))) or 0

    def count_claims_with_evidence(self) -> int:
        # PostgreSQL JSON 列不支持直接比较；使用 json_array_length 统计非空证据数组。
        return (
            self.session.scalar(
                select(func.count(ClaimModel.id)).where(
                    func.json_array_length(ClaimModel.evidence_ids) > 0
                )
            )
            or 0
        )

    def find_claim_by_fingerprint(
        self, event_id: str, fingerprint: str, as_of: Optional[datetime] = None
    ) -> Optional[Claim]:
        statement = select(ClaimModel).where(
            ClaimModel.event_id == event_id,
            ClaimModel.fingerprint == fingerprint,
        )
        if as_of is not None:
            statement = statement.where(ClaimModel.as_of <= as_of)
        model = self.session.scalar(statement)
        return _claim(model) if model else None

    def save_claim_evidence(self, value: ClaimEvidenceRelation) -> None:
        self.session.add(
            ClaimEvidenceRelationModel(
                id=new_id("cer"),
                claim_id=value.claim_id,
                evidence_id=value.evidence_id,
                stance=value.stance,
                source_independence_key=value.source_independence_key,
                weight=Decimal(str(value.weight)),
            )
        )
        self.session.flush()

    def save_conflict(self, value: ConflictRecord) -> None:
        self.session.add(
            ConflictModel(
                id=value.id,
                event_id=value.event_id,
                conflict_type=value.conflict_type,
                severity=value.severity,
                status=value.status,
                summary=value.summary,
                claim_ids=value.claim_ids,
                resolution=value.resolution,
                version=value.version,
            )
        )
        self.session.flush()

    def get_conflict(self, conflict_id: str) -> Optional[ConflictRecord]:
        model = self.session.get(ConflictModel, conflict_id)
        return _conflict(model) if model else None

    def update_conflict(self, value: ConflictRecord) -> None:
        model = self.session.get(ConflictModel, value.id)
        if not model:
            raise KeyError(f"Conflict not found: {value.id}")
        model.status = value.status
        model.resolution = value.resolution
        model.version = value.version
        self.session.flush()

    def list_claim_evidence(self, claim_id: str) -> list[ClaimEvidenceRelation]:
        models = self.session.scalars(
            select(ClaimEvidenceRelationModel).where(
                ClaimEvidenceRelationModel.claim_id == claim_id
            )
        )
        return [
            ClaimEvidenceRelation(
                claim_id=model.claim_id,
                evidence_id=model.evidence_id,
                stance=model.stance,
                source_independence_key=model.source_independence_key,
                weight=float(model.weight),
            )
            for model in models
        ]

    def list_conflicts_for_event(
        self, event_id: str, as_of: Optional[datetime] = None
    ) -> list[ConflictRecord]:
        # ConflictModel 当前无时间戳列，as_of 过滤暂不适用；保留参数供后续扩展。
        del as_of
        models = self.session.scalars(
            select(ConflictModel).where(ConflictModel.event_id == event_id)
        )
        return [
            ConflictRecord(
                id=model.id,
                event_id=model.event_id,
                conflict_type=model.conflict_type,
                severity=model.severity,
                status=model.status,
                summary=model.summary,
                claim_ids=model.claim_ids,
                resolution=model.resolution,
                version=model.version,
            )
            for model in models
        ]

    def save_fact_card(self, value: FactCard) -> None:
        if self.session.get(FactCardModel, value.id) is not None:
            raise ReportVersionConflict("REPORT_VERSION_IMMUTABLE")
        try:
            with self.session.begin_nested():
                self.session.add(FactCardModel(**value.__dict__))
                self.session.flush()
        except IntegrityError as exc:
            # event_id/version 的数据库唯一约束是并发写入的最终保护。
            raise ReportVersionConflict("REPORT_VERSION_CONFLICT") from exc

    def save_impact_analysis(self, value: ImpactAnalysis) -> None:
        if self.session.get(ImpactAnalysisModel, value.id) is not None:
            raise ReportVersionConflict("IMPACT_ANALYSIS_IMMUTABLE")
        try:
            with self.session.begin_nested():
                self.session.add(ImpactAnalysisModel(**value.__dict__))
                self.session.flush()
        except IntegrityError as exc:
            raise ReportVersionConflict("IMPACT_ANALYSIS_VERSION_CONFLICT") from exc

    def get_impact_analysis(self, impact_analysis_id: str) -> Optional[ImpactAnalysis]:
        model = self.session.get(ImpactAnalysisModel, impact_analysis_id)
        return _impact_analysis(model) if model else None

    def get_latest_impact_analysis_for_event(self, event_id: str) -> Optional[ImpactAnalysis]:
        statement = (
            select(ImpactAnalysisModel)
            .where(
                ImpactAnalysisModel.event_id == event_id,
                ImpactAnalysisModel.status != "superseded",
            )
            .order_by(ImpactAnalysisModel.version.desc(), ImpactAnalysisModel.created_at.desc())
            .limit(1)
        )
        model = self.session.scalar(statement)
        return _impact_analysis(model) if model else None

    def list_impact_analyses_for_event(
        self, event_id: str, limit: Optional[int] = None
    ) -> list[ImpactAnalysis]:
        statement = (
            select(ImpactAnalysisModel)
            .where(ImpactAnalysisModel.event_id == event_id)
            .order_by(ImpactAnalysisModel.version.desc(), ImpactAnalysisModel.created_at.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        models = self.session.scalars(statement)
        return [_impact_analysis(model) for model in models]

    def update_impact_analysis(self, value: ImpactAnalysis) -> None:
        model = self.session.get(ImpactAnalysisModel, value.id)
        if model is None:
            raise KeyError(f"impact_analysis not found: {value.id}")
        model.status = value.status
        model.analysis_payload = value.analysis_payload or {}
        model.quality_report = value.quality_report or {}
        model.edit_revision = value.edit_revision
        model.derived_from_id = value.derived_from_id
        model.preliminary_assessment_id = value.preliminary_assessment_id
        self.session.flush()

    def save_preliminary_assessment(self, value: EventPreliminaryAssessment) -> None:
        if self.session.get(EventPreliminaryAssessmentModel, value.id) is not None:
            raise ReportVersionConflict("PRELIMINARY_ASSESSMENT_IMMUTABLE")
        try:
            with self.session.begin_nested():
                self.session.add(EventPreliminaryAssessmentModel(**value.__dict__))
                self.session.flush()
        except IntegrityError as exc:
            raise ReportVersionConflict("PRELIMINARY_ASSESSMENT_VERSION_CONFLICT") from exc

    def get_preliminary_assessment(
        self, assessment_id: str
    ) -> Optional[EventPreliminaryAssessment]:
        model = self.session.get(EventPreliminaryAssessmentModel, assessment_id)
        return _preliminary_assessment(model) if model else None

    def get_latest_preliminary_assessment_for_event(
        self, event_id: str
    ) -> Optional[EventPreliminaryAssessment]:
        statement = (
            select(EventPreliminaryAssessmentModel)
            .where(
                EventPreliminaryAssessmentModel.event_id == event_id,
                EventPreliminaryAssessmentModel.status != "superseded",
            )
            .order_by(
                EventPreliminaryAssessmentModel.version.desc(),
                EventPreliminaryAssessmentModel.created_at.desc(),
            )
            .limit(1)
        )
        model = self.session.scalar(statement)
        return _preliminary_assessment(model) if model else None

    def list_preliminary_assessments_for_event(
        self, event_id: str, limit: Optional[int] = None
    ) -> list[EventPreliminaryAssessment]:
        statement = (
            select(EventPreliminaryAssessmentModel)
            .where(EventPreliminaryAssessmentModel.event_id == event_id)
            .order_by(
                EventPreliminaryAssessmentModel.version.desc(),
                EventPreliminaryAssessmentModel.created_at.desc(),
            )
        )
        if limit is not None:
            statement = statement.limit(limit)
        return [_preliminary_assessment(model) for model in self.session.scalars(statement)]

    def update_preliminary_assessment(self, value: EventPreliminaryAssessment) -> None:
        model = self.session.get(EventPreliminaryAssessmentModel, value.id)
        if model is None:
            raise KeyError(f"preliminary_assessment not found: {value.id}")
        model.status = value.status
        self.session.flush()

    def get_impact_graph_layout(
        self, analysis_id: str, user_id: str
    ) -> Optional[ImpactGraphLayout]:
        model = self.session.scalar(
            select(ImpactGraphLayoutModel).where(
                ImpactGraphLayoutModel.analysis_id == analysis_id,
                ImpactGraphLayoutModel.user_id == user_id,
            )
        )
        return _impact_graph_layout(model) if model else None

    def save_impact_graph_layout(self, value: ImpactGraphLayout) -> None:
        model = self.session.scalar(
            select(ImpactGraphLayoutModel).where(
                ImpactGraphLayoutModel.analysis_id == value.analysis_id,
                ImpactGraphLayoutModel.user_id == value.user_id,
            )
        )
        data = value.__dict__.copy()
        data["updated_at"] = value.updated_at or datetime.now(timezone.utc)
        if model is None:
            self.session.add(ImpactGraphLayoutModel(id=new_id("igl"), **data))
        else:
            model.node_positions = value.node_positions
            model.collapsed_groups = value.collapsed_groups
            model.viewport = value.viewport
            model.updated_at = data["updated_at"]

    def delete_impact_graph_layout(self, analysis_id: str, user_id: str) -> None:
        model = self.session.scalar(
            select(ImpactGraphLayoutModel).where(
                ImpactGraphLayoutModel.analysis_id == analysis_id,
                ImpactGraphLayoutModel.user_id == user_id,
            )
        )
        if model is not None:
            self.session.delete(model)

    def save_impact_target(self, value: ImpactTargetDefinition) -> None:
        model = self.session.get(ImpactTargetDefinitionModel, value.id)
        if model is None:
            model = ImpactTargetDefinitionModel(**value.__dict__)
            self.session.add(model)
        else:
            for key, item in value.__dict__.items():
                setattr(model, key, item)

    def get_impact_target(self, target_id: str) -> Optional[ImpactTargetDefinition]:
        model = self.session.get(ImpactTargetDefinitionModel, target_id)
        return _impact_target(model) if model else None

    def find_impact_target(
        self, target_type: str, target_code: str, taxonomy_version: str = "default-v1"
    ) -> Optional[ImpactTargetDefinition]:
        model = self.session.scalar(
            select(ImpactTargetDefinitionModel).where(
                ImpactTargetDefinitionModel.target_type == target_type,
                ImpactTargetDefinitionModel.target_code == target_code,
                ImpactTargetDefinitionModel.taxonomy_version == taxonomy_version,
            )
        )
        return _impact_target(model) if model else None

    def list_impact_targets(
        self, target_type: Optional[str] = None
    ) -> list[ImpactTargetDefinition]:
        statement = select(ImpactTargetDefinitionModel).order_by(
            ImpactTargetDefinitionModel.canonical_name
        )
        if target_type:
            statement = statement.where(ImpactTargetDefinitionModel.target_type == target_type)
        return [_impact_target(item) for item in self.session.scalars(statement)]

    def save_market_instrument(self, value: MarketInstrument) -> None:
        model = self.session.get(MarketInstrumentModel, value.id)
        if model is None:
            self.session.add(MarketInstrumentModel(**value.__dict__))
        else:
            for key, item in value.__dict__.items():
                setattr(model, key, item)

    def get_market_instrument(self, instrument_id: str) -> Optional[MarketInstrument]:
        model = self.session.get(MarketInstrumentModel, instrument_id)
        return _market_instrument(model) if model else None

    def list_market_instruments(self, active: Optional[bool] = None) -> list[MarketInstrument]:
        statement = select(MarketInstrumentModel).order_by(
            MarketInstrumentModel.market,
            MarketInstrumentModel.instrument_type,
            MarketInstrumentModel.symbol,
        )
        if active is not None:
            statement = statement.where(MarketInstrumentModel.active == active)
        return [_market_instrument(item) for item in self.session.scalars(statement)]

    def save_industry_taxonomy(self, value: IndustryTaxonomy) -> None:
        model = self.session.get(IndustryTaxonomyModel, value.id)
        data = value.__dict__.copy()
        data["created_at"] = value.created_at or datetime.now(timezone.utc)
        if model is None:
            self.session.add(IndustryTaxonomyModel(**data))
        else:
            for key, item in data.items():
                setattr(model, key, item)

    def list_industry_taxonomies(self, status: Optional[str] = None) -> list[IndustryTaxonomy]:
        statement = select(IndustryTaxonomyModel).order_by(
            IndustryTaxonomyModel.standard, IndustryTaxonomyModel.version
        )
        if status is not None:
            statement = statement.where(IndustryTaxonomyModel.status == status)
        return [_industry_taxonomy(item) for item in self.session.scalars(statement)]

    def save_industry_classification(self, value: IndustryClassification) -> None:
        model = self.session.get(IndustryClassificationModel, value.id)
        if model is None:
            self.session.add(IndustryClassificationModel(**value.__dict__))
        else:
            for key, item in value.__dict__.items():
                setattr(model, key, item)

    def list_industry_classifications(
        self, taxonomy_id: Optional[str] = None
    ) -> list[IndustryClassification]:
        statement = select(IndustryClassificationModel).order_by(
            IndustryClassificationModel.taxonomy_id,
            IndustryClassificationModel.level,
            IndustryClassificationModel.code,
        )
        if taxonomy_id is not None:
            statement = statement.where(IndustryClassificationModel.taxonomy_id == taxonomy_id)
        return [_industry_classification(item) for item in self.session.scalars(statement)]

    def save_instrument_industry_membership(self, value: InstrumentIndustryMembership) -> None:
        model = self.session.get(InstrumentIndustryMembershipModel, value.id)
        data = value.__dict__.copy()
        data["created_at"] = value.created_at or datetime.now(timezone.utc)
        if model is None:
            self.session.add(InstrumentIndustryMembershipModel(**data))
        else:
            for key, item in data.items():
                setattr(model, key, item)

    def list_instrument_industry_memberships(
        self, instrument_id: Optional[str] = None, status: Optional[str] = None
    ) -> list[InstrumentIndustryMembership]:
        statement = select(InstrumentIndustryMembershipModel).order_by(
            InstrumentIndustryMembershipModel.instrument_id,
            InstrumentIndustryMembershipModel.taxonomy_id,
            InstrumentIndustryMembershipModel.industry_code,
        )
        if instrument_id is not None:
            statement = statement.where(
                InstrumentIndustryMembershipModel.instrument_id == instrument_id
            )
        if status is not None:
            statement = statement.where(InstrumentIndustryMembershipModel.status == status)
        return [_instrument_industry_membership(item) for item in self.session.scalars(statement)]

    def save_impact_target_mapping(self, value: ImpactTargetMapping) -> None:
        if self.session.get(ImpactTargetMappingModel, value.id) is not None:
            return
        data = value.__dict__.copy()
        data["created_at"] = value.created_at or datetime.now(timezone.utc)
        self.session.add(ImpactTargetMappingModel(**data))

    def get_impact_target_mapping(self, mapping_id: str) -> Optional[ImpactTargetMapping]:
        model = self.session.get(ImpactTargetMappingModel, mapping_id)
        return _impact_target_mapping(model) if model else None

    def update_impact_target_mapping(self, value: ImpactTargetMapping) -> None:
        model = self.session.get(ImpactTargetMappingModel, value.id)
        if model is None:
            raise KeyError(f"impact target mapping not found: {value.id}")
        for key, item in value.__dict__.items():
            setattr(model, key, item)

    def list_impact_target_mappings(
        self, target_id: Optional[str] = None, status: Optional[str] = None
    ) -> list[ImpactTargetMapping]:
        statement = select(ImpactTargetMappingModel).order_by(
            ImpactTargetMappingModel.target_id,
            ImpactTargetMappingModel.mapping_type,
            ImpactTargetMappingModel.mapping_code,
        )
        if target_id is not None:
            statement = statement.where(ImpactTargetMappingModel.target_id == target_id)
        if status is not None:
            statement = statement.where(ImpactTargetMappingModel.status == status)
        return [_impact_target_mapping(item) for item in self.session.scalars(statement)]

    def save_market_master_data_import_run(self, value: MarketMasterDataImportRun) -> None:
        if self.find_market_master_data_import_run_by_hash(value.source_hash) is not None:
            return
        data = value.__dict__.copy()
        data["created_at"] = value.created_at or datetime.now(timezone.utc)
        self.session.add(MarketMasterDataImportRunModel(**data))

    def get_market_master_data_import_run(self, run_id: str) -> Optional[MarketMasterDataImportRun]:
        model = self.session.get(MarketMasterDataImportRunModel, run_id)
        return _market_master_data_import_run(model) if model else None

    def find_market_master_data_import_run_by_hash(
        self, source_hash: str
    ) -> Optional[MarketMasterDataImportRun]:
        model = self.session.scalar(
            select(MarketMasterDataImportRunModel).where(
                MarketMasterDataImportRunModel.source_hash == source_hash
            )
        )
        return _market_master_data_import_run(model) if model else None

    def update_market_master_data_import_run(self, value: MarketMasterDataImportRun) -> None:
        model = self.session.get(MarketMasterDataImportRunModel, value.id)
        if model is None:
            raise KeyError(f"market master import run not found: {value.id}")
        for key, item in value.__dict__.items():
            setattr(model, key, item)

    def list_market_master_data_import_runs(self) -> list[MarketMasterDataImportRun]:
        statement = select(MarketMasterDataImportRunModel).order_by(
            MarketMasterDataImportRunModel.created_at.desc()
        )
        return [_market_master_data_import_run(item) for item in self.session.scalars(statement)]

    def save_event_impact_relation(self, value: EventImpactRelation) -> None:
        model = self.session.get(EventImpactRelationModel, value.id)
        if model is None:
            self.session.add(EventImpactRelationModel(**value.__dict__))
        else:
            for key, item in value.__dict__.items():
                setattr(model, key, item)

    def list_event_impact_relations(
        self, event_id: Optional[str] = None
    ) -> list[EventImpactRelation]:
        statement = select(EventImpactRelationModel).order_by(EventImpactRelationModel.created_at)
        if event_id:
            statement = statement.where(
                (EventImpactRelationModel.source_event_id == event_id)
                | (EventImpactRelationModel.target_event_id == event_id)
            )
        return [_event_impact_relation(item) for item in self.session.scalars(statement)]

    def save_impact_contribution(self, value: ImpactContribution) -> None:
        if self.session.get(ImpactContributionModel, value.id) is not None:
            return
        self.session.add(ImpactContributionModel(**value.__dict__))

    def list_impact_contributions(
        self, target_id: Optional[str] = None
    ) -> list[ImpactContribution]:
        statement = select(ImpactContributionModel).order_by(ImpactContributionModel.created_at)
        if target_id:
            statement = statement.where(ImpactContributionModel.target_id == target_id)
        return [_impact_contribution(item) for item in self.session.scalars(statement)]

    def save_impact_dimension_contribution(self, value: ImpactDimensionContribution) -> None:
        if self.session.get(ImpactDimensionContributionModel, value.id) is None:
            self.session.add(ImpactDimensionContributionModel(**value.__dict__))

    def list_impact_dimension_contributions(
        self, contribution_id: Optional[str] = None
    ) -> list[ImpactDimensionContribution]:
        statement = select(ImpactDimensionContributionModel).order_by(
            ImpactDimensionContributionModel.id
        )
        if contribution_id:
            statement = statement.where(
                ImpactDimensionContributionModel.contribution_id == contribution_id
            )
        return [_impact_dimension_contribution(item) for item in self.session.scalars(statement)]

    def save_target_impact_snapshot(
        self, value: TargetImpactSnapshot, contributions: list[TargetImpactSnapshotContribution]
    ) -> None:
        model = self.session.get(TargetImpactSnapshotModel, value.id)
        if model is None:
            self.session.add(TargetImpactSnapshotModel(**value.__dict__))
        for item in contributions:
            existing = self.session.scalar(
                select(TargetImpactSnapshotContributionModel).where(
                    TargetImpactSnapshotContributionModel.snapshot_id == item.snapshot_id,
                    TargetImpactSnapshotContributionModel.contribution_id == item.contribution_id,
                )
            )
            if existing is None:
                self.session.add(
                    TargetImpactSnapshotContributionModel(id=new_id("tic"), **item.__dict__)
                )

    def get_latest_target_impact_snapshot(
        self,
        target_id: str,
        horizon: Optional[str] = None,
        scenario_set_id: str = "baseline",
        as_of: Optional[datetime] = None,
    ) -> Optional[TargetImpactSnapshot]:
        statement = (
            select(TargetImpactSnapshotModel)
            .where(
                TargetImpactSnapshotModel.target_id == target_id,
                TargetImpactSnapshotModel.scenario_set_id == scenario_set_id,
            )
            .order_by(
                TargetImpactSnapshotModel.as_of.desc(), TargetImpactSnapshotModel.created_at.desc()
            )
            .limit(1)
        )
        if horizon:
            statement = statement.where(TargetImpactSnapshotModel.horizon == horizon)
        if as_of is not None:
            statement = statement.where(TargetImpactSnapshotModel.as_of <= as_of)
        model = self.session.scalar(statement)
        return _target_impact_snapshot(model) if model else None

    def list_target_impact_snapshot_contributions(
        self, snapshot_id: str
    ) -> list[TargetImpactSnapshotContribution]:
        statement = select(TargetImpactSnapshotContributionModel).where(
            TargetImpactSnapshotContributionModel.snapshot_id == snapshot_id
        )
        return [
            _target_impact_snapshot_contribution(item) for item in self.session.scalars(statement)
        ]

    def save_market_forecast_run(self, value: MarketForecastRun) -> None:
        if self.find_market_forecast_run_by_source_hash(value.source_hash) is None:
            self.session.add(MarketForecastRunModel(**value.__dict__))

    def get_market_forecast_run(self, forecast_id: str) -> Optional[MarketForecastRun]:
        model = self.session.get(MarketForecastRunModel, forecast_id)
        return _market_forecast_run(model) if model else None

    def find_market_forecast_run_by_source_hash(
        self, source_hash: str
    ) -> Optional[MarketForecastRun]:
        model = self.session.scalar(
            select(MarketForecastRunModel).where(MarketForecastRunModel.source_hash == source_hash)
        )
        return _market_forecast_run(model) if model else None

    def list_market_forecast_runs(
        self,
        instrument_id: Optional[str] = None,
        horizon: Optional[int] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 500,
    ) -> list[MarketForecastRun]:
        statement = select(MarketForecastRunModel).order_by(
            MarketForecastRunModel.as_of.desc(), MarketForecastRunModel.id.desc()
        )
        if instrument_id is not None:
            statement = statement.where(MarketForecastRunModel.instrument_id == instrument_id)
        if horizon is not None:
            statement = statement.where(MarketForecastRunModel.horizon == horizon)
        if start is not None:
            statement = statement.where(MarketForecastRunModel.as_of >= start)
        if end is not None:
            statement = statement.where(MarketForecastRunModel.as_of <= end)
        return [_market_forecast_run(item) for item in self.session.scalars(statement.limit(limit))]

    def save_market_forecast_outcome(self, value: MarketForecastOutcome) -> None:
        if self.get_market_forecast_outcome(value.forecast_id) is None:
            self.session.add(MarketForecastOutcomeModel(**value.__dict__))

    def get_market_forecast_outcome(self, forecast_id: str) -> Optional[MarketForecastOutcome]:
        model = self.session.scalar(
            select(MarketForecastOutcomeModel).where(
                MarketForecastOutcomeModel.forecast_id == forecast_id
            )
        )
        return _market_forecast_outcome(model) if model else None

    def list_market_forecast_outcomes(
        self, forecast_ids: Optional[list[str]] = None
    ) -> list[MarketForecastOutcome]:
        statement = select(MarketForecastOutcomeModel).order_by(
            MarketForecastOutcomeModel.outcome_observed_at.desc(),
            MarketForecastOutcomeModel.id.desc(),
        )
        if forecast_ids is not None:
            if not forecast_ids:
                return []
            statement = statement.where(MarketForecastOutcomeModel.forecast_id.in_(forecast_ids))
        return [_market_forecast_outcome(item) for item in self.session.scalars(statement)]

    def save_market_calibration_version(self, value: MarketCalibrationVersion) -> None:
        statement = select(MarketCalibrationVersionModel.id).where(
            MarketCalibrationVersionModel.model_key == value.model_key,
            MarketCalibrationVersionModel.version == value.version,
            MarketCalibrationVersionModel.horizon == value.horizon,
            MarketCalibrationVersionModel.market == value.market,
        )
        if self.session.scalar(statement) is None:
            self.session.add(MarketCalibrationVersionModel(**value.__dict__))

    def get_market_calibration_version(
        self, calibration_id: str
    ) -> Optional[MarketCalibrationVersion]:
        model = self.session.get(MarketCalibrationVersionModel, calibration_id)
        return _market_calibration_version(model) if model else None

    def update_market_calibration_version(self, value: MarketCalibrationVersion) -> None:
        model = self.session.get(MarketCalibrationVersionModel, value.id)
        if model is None:
            raise KeyError(f"market calibration not found: {value.id}")
        model.status = value.status
        model.published_at = value.published_at

    def list_market_calibration_versions(
        self,
        model_key: Optional[str] = None,
        market: Optional[str] = None,
        horizon: Optional[int] = None,
        status: Optional[str] = None,
    ) -> list[MarketCalibrationVersion]:
        statement = select(MarketCalibrationVersionModel).order_by(
            MarketCalibrationVersionModel.created_at.desc(),
            MarketCalibrationVersionModel.id.desc(),
        )
        filters = (
            (MarketCalibrationVersionModel.model_key, model_key),
            (MarketCalibrationVersionModel.market, market),
            (MarketCalibrationVersionModel.horizon, horizon),
            (MarketCalibrationVersionModel.status, status),
        )
        for column, value in filters:
            if value is not None:
                statement = statement.where(column == value)
        return [_market_calibration_version(item) for item in self.session.scalars(statement)]

    def save_forward_impact_window(self, value: ForwardImpactWindow) -> None:
        model = self.session.get(ForwardImpactWindowModel, value.id)
        if model is None:
            self.session.add(ForwardImpactWindowModel(**value.__dict__))
        else:
            for key, item in value.__dict__.items():
                setattr(model, key, item)

    def get_forward_impact_window(self, window_id: str) -> Optional[ForwardImpactWindow]:
        model = self.session.get(ForwardImpactWindowModel, window_id)
        return _forward_impact_window(model) if model else None

    def save_forward_catalyst(self, value: ForwardCatalyst) -> None:
        model = self.session.get(ForwardCatalystModel, value.id)
        if model is None:
            self.session.add(ForwardCatalystModel(**value.__dict__))
        else:
            for key, item in value.__dict__.items():
                setattr(model, key, item)

    def get_forward_catalyst(self, catalyst_id: str) -> Optional[ForwardCatalyst]:
        model = self.session.get(ForwardCatalystModel, catalyst_id)
        return _forward_catalyst(model) if model else None

    def list_forward_catalysts(self, target_id: Optional[str] = None) -> list[ForwardCatalyst]:
        statement = select(ForwardCatalystModel).order_by(ForwardCatalystModel.created_at)
        if target_id:
            statement = statement.where(ForwardCatalystModel.target_id == target_id)
        return [_forward_catalyst(item) for item in self.session.scalars(statement)]

    def save_forward_contribution(self, value: ForwardImpactContribution) -> None:
        if self.session.get(ForwardImpactContributionModel, value.id) is None:
            self.session.add(ForwardImpactContributionModel(**value.__dict__))

    def list_forward_contributions(self, window_id: str) -> list[ForwardImpactContribution]:
        statement = select(ForwardImpactContributionModel).where(
            ForwardImpactContributionModel.window_id == window_id
        )
        return [_forward_impact_contribution(item) for item in self.session.scalars(statement)]

    def save_forward_points(self, values: list[ForwardImpactPoint]) -> None:
        for value in values:
            if self.session.get(ForwardImpactPointModel, value.id) is None:
                self.session.add(ForwardImpactPointModel(**value.__dict__))

    def list_forward_points(
        self, window_id: str, scenario_id: str = "baseline"
    ) -> list[ForwardImpactPoint]:
        statement = (
            select(ForwardImpactPointModel)
            .where(
                ForwardImpactPointModel.window_id == window_id,
                ForwardImpactPointModel.scenario_id == scenario_id,
            )
            .order_by(ForwardImpactPointModel.point_at)
        )
        return [_forward_impact_point(item) for item in self.session.scalars(statement)]

    def save_future_event(self, value: FutureEvent) -> None:
        model = self.session.get(FutureEventModel, value.id)
        if model is None:
            self.session.add(FutureEventModel(**value.__dict__))
        else:
            for key, item in value.__dict__.items():
                setattr(model, key, item)

    def get_future_event(self, event_id: str) -> Optional[FutureEvent]:
        model = self.session.get(FutureEventModel, event_id)
        return _future_event(model) if model else None

    def list_future_events(self) -> list[FutureEvent]:
        statement = select(FutureEventModel).order_by(FutureEventModel.created_at)
        return [_future_event(item) for item in self.session.scalars(statement)]

    def save_future_event_revision(self, value: FutureEventRevision) -> None:
        if self.session.get(FutureEventRevisionModel, value.id) is not None:
            return
        self.session.add(FutureEventRevisionModel(**value.__dict__))
        event = self.session.get(FutureEventModel, value.future_event_id)
        if event is not None and (
            event.current_revision_id is None
            or value.revision_no
            >= self.session.get(FutureEventRevisionModel, event.current_revision_id).revision_no
        ):
            event.current_revision_id = value.id

    def get_future_event_revision(self, revision_id: str) -> Optional[FutureEventRevision]:
        model = self.session.get(FutureEventRevisionModel, revision_id)
        return _future_event_revision(model) if model else None

    def list_future_event_revisions(self, event_id: str) -> list[FutureEventRevision]:
        statement = (
            select(FutureEventRevisionModel)
            .where(FutureEventRevisionModel.future_event_id == event_id)
            .order_by(FutureEventRevisionModel.revision_no)
        )
        return [_future_event_revision(item) for item in self.session.scalars(statement)]

    def save_future_event_target_impact(self, value: FutureEventTargetImpact) -> None:
        model = self.session.get(FutureEventTargetImpactModel, value.id)
        if model is None:
            self.session.add(FutureEventTargetImpactModel(**value.__dict__))
        else:
            for key, item in value.__dict__.items():
                setattr(model, key, item)

    def list_future_event_target_impacts(
        self, event_id: Optional[str] = None, target_id: Optional[str] = None
    ) -> list[FutureEventTargetImpact]:
        statement = select(FutureEventTargetImpactModel).order_by(
            FutureEventTargetImpactModel.created_at
        )
        if event_id:
            statement = statement.where(FutureEventTargetImpactModel.future_event_id == event_id)
        if target_id:
            statement = statement.where(FutureEventTargetImpactModel.target_id == target_id)
        return [_future_event_target_impact(item) for item in self.session.scalars(statement)]

    # Agent Runtime (DD-80)
    def save_agent_registration(self, registration: AgentRegistration) -> None:
        now = datetime.now(timezone.utc)
        existing = self.session.get(AgentRegistrationModel, registration.agent_key)
        if existing is not None:
            existing.version = registration.version
            existing.display_name = registration.display_name
            existing.capabilities = registration.capabilities
            existing.input_schema_refs = registration.input_schema_refs
            existing.output_schema_ref = registration.output_schema_ref
            existing.allowed_tools = registration.allowed_tools
            existing.budget_profile = registration.budget_profile
            existing.quality_gates = registration.quality_gates
            existing.config = registration.config
            existing.updated_at = now
        else:
            self.session.add(
                AgentRegistrationModel(
                    agent_key=registration.agent_key,
                    version=registration.version,
                    display_name=registration.display_name,
                    capabilities=registration.capabilities,
                    input_schema_refs=registration.input_schema_refs,
                    output_schema_ref=registration.output_schema_ref,
                    allowed_tools=registration.allowed_tools,
                    budget_profile=registration.budget_profile,
                    quality_gates=registration.quality_gates,
                    config=registration.config,
                    created_at=registration.created_at or now,
                    updated_at=registration.updated_at or now,
                )
            )
        self.session.flush()

    def get_agent_registration(self, agent_key: str) -> Optional[AgentRegistration]:
        model = self.session.get(AgentRegistrationModel, agent_key)
        return _agent_registration(model) if model else None

    def list_agent_registrations(self) -> list[AgentRegistration]:
        models = self.session.scalars(
            select(AgentRegistrationModel).order_by(AgentRegistrationModel.agent_key)
        )
        return [_agent_registration(model) for model in models]

    def save_research_plan(self, plan: ResearchPlan) -> None:
        if self.session.get(ResearchPlanModel, plan.id) is not None:
            raise ReportVersionConflict("RESEARCH_PLAN_IMMUTABLE")
        try:
            with self.session.begin_nested():
                self.session.add(
                    ResearchPlanModel(
                        id=plan.id,
                        workflow_id=plan.workflow_id,
                        question=plan.question,
                        objective=plan.objective,
                        as_of=plan.as_of,
                        status=plan.status,
                        budget_profile=plan.budget_profile,
                        completion_criteria=plan.completion_criteria,
                        plan_metadata=plan.metadata,
                        created_at=plan.created_at or datetime.now(timezone.utc),
                        updated_at=plan.updated_at,
                    )
                )
                self.session.flush()
        except IntegrityError as exc:
            raise ReportVersionConflict("RESEARCH_PLAN_CONFLICT") from exc

    def get_research_plan(self, plan_id: str) -> Optional[ResearchPlan]:
        model = self.session.get(ResearchPlanModel, plan_id)
        if model is None:
            return None
        tasks = self.list_research_tasks(plan_id)
        return _research_plan(model, tasks)

    def get_research_plan_by_workflow(self, workflow_id: str) -> Optional[ResearchPlan]:
        model = self.session.scalar(
            select(ResearchPlanModel).where(ResearchPlanModel.workflow_id == workflow_id)
        )
        if model is None:
            return None
        tasks = self.list_research_tasks(model.id)
        return _research_plan(model, tasks)

    def list_research_plans(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[ResearchPlan]:
        statement = select(ResearchPlanModel).order_by(
            ResearchPlanModel.created_at.desc(), ResearchPlanModel.id.desc()
        )
        if status:
            statement = statement.where(ResearchPlanModel.status == status)
        if cursor:
            statement = statement.where(
                _cursor_filter(ResearchPlanModel.created_at, ResearchPlanModel.id, cursor)
            )
        if limit is not None:
            statement = statement.limit(limit)
        models = self.session.scalars(statement)
        return [_research_plan(model, self.list_research_tasks(model.id)) for model in models]

    def update_research_plan(self, plan: ResearchPlan) -> None:
        model = self.session.get(ResearchPlanModel, plan.id)
        if model is None:
            raise KeyError(f"research_plan not found: {plan.id}")
        model.status = plan.status
        model.updated_at = plan.updated_at or datetime.now(timezone.utc)
        model.completion_criteria = plan.completion_criteria
        model.plan_metadata = plan.metadata
        self.session.flush()

    def save_research_task(self, task: ResearchTask) -> None:
        existing = self.session.get(ResearchTaskModel, task.id)
        if existing is not None:
            for field, field_value in task.__dict__.items():
                if field != "id":
                    setattr(existing, field, field_value)
        else:
            self.session.add(
                ResearchTaskModel(
                    id=task.id,
                    plan_id=task.plan_id,
                    name=task.name,
                    agent_key=task.agent_key,
                    description=task.description,
                    dependencies=task.dependencies,
                    required=task.required,
                    status=task.status,
                    input_fields=task.input_fields,
                    output_field=task.output_field,
                    tool_strategy=task.tool_strategy,
                    output_schema=task.output_schema,
                    input_hash=task.input_hash,
                    output_snapshot=task.output_snapshot,
                    review_reason=task.review_reason,
                    started_at=task.started_at,
                    ended_at=task.ended_at,
                    created_at=task.created_at or datetime.now(timezone.utc),
                )
            )
        self.session.flush()

    def get_research_task(self, task_id: str) -> Optional[ResearchTask]:
        model = self.session.get(ResearchTaskModel, task_id)
        return _research_task(model) if model else None

    def list_research_tasks(self, plan_id: str) -> list[ResearchTask]:
        models = self.session.scalars(
            select(ResearchTaskModel)
            .where(ResearchTaskModel.plan_id == plan_id)
            .order_by(ResearchTaskModel.created_at)
        )
        return [_research_task(model) for model in models]

    def update_research_task(self, task: ResearchTask) -> None:
        model = self.session.get(ResearchTaskModel, task.id)
        if model is None:
            raise KeyError(f"research_task not found: {task.id}")
        for field, field_value in task.__dict__.items():
            if field != "id":
                setattr(model, field, field_value)
        self.session.flush()

    def list_events(
        self,
        as_of: Optional[datetime] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        *,
        event_types: Optional[list[str]] = None,
        entity_ids: Optional[list[str]] = None,
        occurred_from: Optional[datetime] = None,
        occurred_to: Optional[datetime] = None,
    ) -> list[Event]:
        statement = select(EventModel).order_by(EventModel.occurred_at.desc(), EventModel.id.desc())
        if as_of is not None:
            statement = statement.where(EventModel.occurred_at <= as_of)
        if event_types:
            statement = statement.where(EventModel.event_type.in_(event_types))
        if occurred_from is not None:
            statement = statement.where(EventModel.occurred_at >= occurred_from)
        if occurred_to is not None:
            statement = statement.where(EventModel.occurred_at <= occurred_to)
        if entity_ids:
            entity_events = select(EventEntityModel.event_id).where(
                EventEntityModel.entity_id.in_(entity_ids)
            )
            statement = statement.where(EventModel.id.in_(entity_events))
        if cursor:
            statement = statement.where(
                _cursor_filter(EventModel.occurred_at, EventModel.id, cursor)
            )
        if limit is not None:
            statement = statement.limit(limit)
        models = self.session.scalars(statement)
        return [_event(value) for value in models]

    def get_document(
        self, document_id: str, *, include_deleted: bool = False
    ) -> Optional[Document]:
        model = self.session.get(DocumentModel, document_id)
        if model is None:
            return None
        document = _document(model)
        if document.deleted_at is not None and not include_deleted:
            return None
        return document

    def get_event(self, event_id: str) -> Optional[Event]:
        model = self.session.get(EventModel, event_id)
        return _event(model) if model else None

    def find_event_by_document(
        self, document_id: str, as_of: Optional[datetime] = None
    ) -> Optional[Event]:
        statement = select(EventModel)
        if as_of is not None:
            statement = statement.where(EventModel.occurred_at <= as_of)
        for model in self.session.scalars(statement):
            if document_id in model.document_ids:
                return _event(model)
        return None

    def get_evidence(
        self, evidence_id: str, *, include_deleted: bool = False
    ) -> Optional[EvidenceSpan]:
        model = self.session.get(EvidenceSpanModel, evidence_id)
        if model is None:
            return None
        evidence = _evidence(model)
        if evidence.deleted_at is not None and not include_deleted:
            return None
        if not include_deleted:
            document = self.get_document(evidence.document_id, include_deleted=True)
            if document is not None and document.deleted_at is not None:
                return None
        return evidence

    def set_document_retention_hold(self, document_id: str, hold: bool) -> Document:
        model = self.session.get(DocumentModel, document_id)
        if model is None:
            raise KeyError(f"Document not found: {document_id}")
        model.retention_hold = hold
        self.session.flush()
        return _document(model)

    def soft_delete_document(
        self, document_id: str, *, deleted_at: Optional[datetime] = None
    ) -> Document:
        model = self.session.get(DocumentModel, document_id)
        if model is None:
            raise KeyError(f"Document not found: {document_id}")
        if model.deleted_at is not None:
            return _document(model)
        if model.retention_hold:
            raise RetentionHoldError(document_id)
        when = deleted_at or datetime.now(timezone.utc)
        model.deleted_at = when
        for evidence in self.session.scalars(
            select(EvidenceSpanModel).where(
                EvidenceSpanModel.document_id == document_id,
                EvidenceSpanModel.deleted_at.is_(None),
            )
        ):
            evidence.deleted_at = when
        self.session.flush()
        return _document(model)

    def purge_document(
        self,
        document_id: str,
        *,
        purged_at: Optional[datetime] = None,
        min_soft_delete_age_seconds: int = 0,
    ) -> Document:
        model = self.session.get(DocumentModel, document_id)
        if model is None:
            raise KeyError(f"Document not found: {document_id}")
        if model.purged_at is not None:
            return _document(model)
        if model.retention_hold:
            raise RetentionHoldError(document_id)
        if model.deleted_at is None:
            raise DocumentNotSoftDeletedError(document_id)
        when = purged_at or datetime.now(timezone.utc)
        _assert_purge_retention_window(
            document_id,
            model.deleted_at,
            min_soft_delete_age_seconds=min_soft_delete_age_seconds,
            now=when,
        )
        model.title = "[purged]"
        model.content = ""
        model.purged_at = when
        for evidence in self.session.scalars(
            select(EvidenceSpanModel).where(EvidenceSpanModel.document_id == document_id)
        ):
            self.session.delete(evidence)
        self.session.flush()
        return _document(model)

    def list_documents_eligible_for_purge(
        self,
        *,
        deleted_before: datetime,
        limit: int = 100,
    ) -> list[Document]:
        statement = (
            select(DocumentModel)
            .where(
                DocumentModel.deleted_at.is_not(None),
                DocumentModel.purged_at.is_(None),
                DocumentModel.retention_hold.is_(False),
                DocumentModel.deleted_at <= deleted_before,
            )
            .order_by(DocumentModel.deleted_at.asc(), DocumentModel.id.asc())
            .limit(max(1, limit))
        )
        return [_document(model) for model in self.session.scalars(statement)]

    def get_claim(self, claim_id: str) -> Optional[Claim]:
        model = self.session.get(ClaimModel, claim_id)
        return _claim(model) if model else None

    def get_claims_for_event(self, event_id: str, as_of: Optional[datetime] = None) -> list[Claim]:
        statement = select(ClaimModel).where(ClaimModel.event_id == event_id)
        if as_of is not None:
            statement = statement.where(ClaimModel.as_of <= as_of)
        models = self.session.scalars(statement)
        return [_claim(value) for value in models]

    def get_fact_card(self, fact_card_id: str) -> Optional[FactCard]:
        model = self.session.get(FactCardModel, fact_card_id)
        return _fact_card(model) if model else None

    def get_fact_card_for_event(
        self, event_id: str, as_of: Optional[datetime] = None
    ) -> Optional[FactCard]:
        statement = (
            select(FactCardModel)
            .where(FactCardModel.event_id == event_id)
            .order_by(FactCardModel.version.desc())
        )
        if as_of is not None:
            statement = statement.where(FactCardModel.as_of <= as_of)
        model = self.session.scalar(statement)
        return _fact_card(model) if model else None

    def list_fact_cards_for_event(self, event_id: str) -> list[FactCard]:
        statement = (
            select(FactCardModel)
            .where(FactCardModel.event_id == event_id)
            .order_by(FactCardModel.version.desc())
        )
        return [_fact_card(model) for model in self.session.scalars(statement)]

    def list_fact_cards(
        self,
        event_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[FactCard]:
        statement = select(FactCardModel).order_by(
            FactCardModel.as_of.desc(),
            FactCardModel.id.desc(),
        )
        if event_id is not None:
            statement = statement.where(FactCardModel.event_id == event_id)
        if status is not None:
            statement = statement.where(FactCardModel.status == status)
        if cursor:
            statement = statement.where(
                _cursor_filter(FactCardModel.as_of, FactCardModel.id, cursor)
            )
        if limit is not None:
            statement = statement.limit(limit)
        return [_fact_card(model) for model in self.session.scalars(statement)]

    def list_published_reports(self, start: datetime, end: datetime) -> list[FactCard]:
        statement = (
            select(FactCardModel)
            .where(FactCardModel.status == "published")
            .where(FactCardModel.as_of >= start)
            .where(FactCardModel.as_of < end)
            .order_by(FactCardModel.as_of)
        )
        return [_fact_card(model) for model in self.session.scalars(statement)]

    def save_brief(self, value: Brief) -> None:
        self.session.add(
            BriefModel(
                id=value.id,
                brief_date=value.brief_date,
                entries=[_brief_entry_dict(entry) for entry in value.entries],
                candidate_count=value.candidate_count,
                rule_version=value.rule_version,
                generated_at=value.generated_at or datetime.now(timezone.utc),
            )
        )
        self.session.flush()

    def get_brief_by_date(self, brief_date: str) -> Optional[Brief]:
        model = self.session.scalar(select(BriefModel).where(BriefModel.brief_date == brief_date))
        return _brief(model) if model else None

    def get_idempotent(self, key: str) -> Optional[PipelineResultReference]:
        model = self.session.get(IdempotencyModel, key)
        if not model:
            return None
        return PipelineResultReference(
            model.document_id,
            model.event_id,
            model.fact_card_id,
            model.request_hash,
        )

    def save_idempotent(self, key: str, value: PipelineResultReference) -> None:
        self.session.add(
            IdempotencyModel(
                key=key,
                request_hash=value.request_hash,
                document_id=value.document_id,
                event_id=value.event_id,
                fact_card_id=value.fact_card_id,
                created_at=datetime.now(timezone.utc),
            )
        )

    def get_api_idempotent(self, key: str) -> Optional[ApiIdempotencyRecord]:
        model = self.session.get(IdempotencyModel, key)
        if not model:
            return None
        try:
            response = json.loads(model.fact_card_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("IDEMPOTENCY_RECORD_INVALID") from exc
        if not isinstance(response, dict):
            raise RuntimeError("IDEMPOTENCY_RECORD_INVALID")
        return ApiIdempotencyRecord(
            request_hash=model.request_hash,
            operation=model.document_id,
            resource_id=model.event_id,
            response=response,
        )

    def save_api_idempotent(self, key: str, value: ApiIdempotencyRecord) -> None:
        self.session.add(
            IdempotencyModel(
                key=key,
                request_hash=value.request_hash,
                document_id=value.operation,
                event_id=value.resource_id,
                fact_card_id=json.dumps(
                    value.response,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                created_at=datetime.now(timezone.utc),
            )
        )
        self.session.flush()

    def add_outbox(self, event_type: str, aggregate_id: str, payload: dict) -> None:
        self.session.add(
            OutboxModel(
                id=new_id("msg"),
                event_type=event_type,
                aggregate_id=aggregate_id,
                payload=payload,
                trace_id=new_id("trc"),
                created_at=datetime.now(timezone.utc),
                published_at=None,
                attempts=0,
                next_attempt_at=None,
                last_error=None,
                dead_lettered_at=None,
            )
        )

    def list_pending_outbox(
        self, limit: int, now: Optional[datetime] = None
    ) -> list[OutboxMessage]:
        current = now or datetime.now(timezone.utc)
        models = self.session.scalars(
            select(OutboxModel)
            .where(OutboxModel.published_at.is_(None))
            .where(OutboxModel.dead_lettered_at.is_(None))
            .where(
                (OutboxModel.next_attempt_at.is_(None)) | (OutboxModel.next_attempt_at <= current)
            )
            .order_by(OutboxModel.created_at)
            .limit(limit)
        )
        return [
            OutboxMessage(
                id=value.id,
                event_type=value.event_type,
                aggregate_id=value.aggregate_id,
                payload=value.payload,
                trace_id=value.trace_id,
                attempts=value.attempts,
            )
            for value in models
        ]

    def list_pending_outbox_by_event_type(
        self, event_type: str, limit: int, now: Optional[datetime] = None
    ) -> list[OutboxMessage]:
        current = now or datetime.now(timezone.utc)
        models = self.session.scalars(
            select(OutboxModel)
            .where(OutboxModel.event_type == event_type)
            .where(OutboxModel.published_at.is_(None))
            .where(OutboxModel.dead_lettered_at.is_(None))
            .where(
                (OutboxModel.next_attempt_at.is_(None)) | (OutboxModel.next_attempt_at <= current)
            )
            .order_by(OutboxModel.created_at)
            .limit(limit)
        )
        return [
            OutboxMessage(
                id=value.id,
                event_type=value.event_type,
                aggregate_id=value.aggregate_id,
                payload=value.payload,
                trace_id=value.trace_id,
                attempts=value.attempts,
            )
            for value in models
        ]

    def mark_outbox_published(self, message_id: str, published_at: datetime) -> None:
        model = self.session.get(OutboxModel, message_id)
        if not model:
            raise KeyError(f"Outbox message not found: {message_id}")
        model.published_at = published_at
        model.last_error = None

    def mark_outbox_failed(self, message_id: str, error: str, next_attempt_at: datetime) -> None:
        model = self.session.get(OutboxModel, message_id)
        if not model:
            raise KeyError(f"Outbox message not found: {message_id}")
        model.attempts += 1
        model.last_error = error[:2000]
        model.next_attempt_at = next_attempt_at

    def mark_outbox_dead_lettered(
        self, message_id: str, error: str, dead_lettered_at: datetime
    ) -> None:
        model = self.session.get(OutboxModel, message_id)
        if not model:
            raise KeyError(f"Outbox message not found: {message_id}")
        model.attempts += 1
        model.last_error = error[:2000]
        model.dead_lettered_at = dead_lettered_at

    def list_outbox(
        self,
        dead_lettered: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[OutboxMessage]:
        statement = select(OutboxModel).order_by(
            OutboxModel.created_at.desc(), OutboxModel.id.desc()
        )
        if dead_lettered is True:
            statement = statement.where(OutboxModel.dead_lettered_at.is_not(None))
        elif dead_lettered is False:
            statement = statement.where(OutboxModel.dead_lettered_at.is_(None))
        if cursor:
            statement = statement.where(
                _cursor_filter(OutboxModel.created_at, OutboxModel.id, cursor)
            )
        if limit is not None:
            statement = statement.limit(limit)
        return [_outbox_message(model) for model in self.session.scalars(statement)]

    def get_outbox(self, outbox_id: str) -> Optional[OutboxMessage]:
        model = self.session.get(OutboxModel, outbox_id)
        return _outbox_message(model) if model else None

    def retry_outbox(self, outbox_id: str) -> None:
        model = self.session.get(OutboxModel, outbox_id)
        if not model:
            raise KeyError(f"Outbox message not found: {outbox_id}")
        model.attempts = 0
        model.next_attempt_at = None
        model.last_error = None
        model.dead_lettered_at = None
        self.session.flush()

    def is_inbox_processed(self, consumer: str, message_id: str) -> bool:
        statement = select(InboxModel.id).where(
            InboxModel.consumer == consumer,
            InboxModel.message_id == message_id,
        )
        return self.session.scalar(statement) is not None

    def save_inbox_processed(
        self, consumer: str, message_id: str, result: Optional[dict] = None
    ) -> None:
        now = datetime.now(timezone.utc)
        self.session.add(
            InboxModel(
                id=new_id("inb"),
                consumer=consumer,
                message_id=message_id,
                received_at=now,
                processed_at=now,
                result=result or {},
            )
        )


_MIN_TIMESTAMP = datetime.min.replace(tzinfo=timezone.utc)


def _paginate(values, cursor, limit, timestamp_of):
    ordered = sorted(
        values,
        key=lambda value: (timestamp_of(value) or _MIN_TIMESTAMP, value.id),
        reverse=True,
    )
    if cursor:
        cursor_at, cursor_id = decode_cursor(cursor)
        ordered = [
            value
            for value in ordered
            if (timestamp_of(value) or _MIN_TIMESTAMP, value.id) < (cursor_at, cursor_id)
        ]
    if limit is not None:
        ordered = ordered[:limit]
    return ordered


def _cursor_filter(timestamp_column, id_column, cursor: str):
    cursor_at, cursor_id = decode_cursor(cursor)
    return (timestamp_column < cursor_at) | (
        (timestamp_column == cursor_at) & (id_column < cursor_id)
    )


def _outbox_message_dict(value: dict) -> OutboxMessage:
    return OutboxMessage(
        id=value["id"],
        event_type=value["event_type"],
        aggregate_id=value["aggregate_id"],
        payload=value["payload"],
        trace_id=value["trace_id"],
        attempts=value["attempts"],
        created_at=value.get("created_at"),
        published_at=value.get("published_at"),
        next_attempt_at=value.get("next_attempt_at"),
        last_error=value.get("last_error"),
        dead_lettered_at=value.get("dead_lettered_at"),
    )


def _quarantine_item(value: QuarantineItemModel) -> QuarantineItem:
    return QuarantineItem(
        id=value.id,
        source_id=value.source_id,
        external_id=value.external_id,
        url=value.url,
        error_code=value.error_code,
        detail=value.detail,
        attempts=value.attempts,
        status=value.status,
        created_at=value.created_at,
    )


def _outbox_message(value: OutboxModel) -> OutboxMessage:
    return OutboxMessage(
        id=value.id,
        event_type=value.event_type,
        aggregate_id=value.aggregate_id,
        payload=value.payload,
        trace_id=value.trace_id,
        attempts=value.attempts,
        created_at=value.created_at,
        published_at=value.published_at,
        next_attempt_at=value.next_attempt_at,
        last_error=value.last_error,
        dead_lettered_at=value.dead_lettered_at,
    )


def _document(value: DocumentModel) -> Document:
    return Document(
        id=value.id,
        source_id=value.source_id,
        source_tier=value.source_tier,
        external_id=value.external_id,
        canonical_url=value.canonical_url,
        title=value.title,
        content=value.content,
        content_hash=value.content_hash,
        published_at=value.published_at,
        ingested_at=value.ingested_at,
        retention_hold=bool(getattr(value, "retention_hold", False)),
        deleted_at=getattr(value, "deleted_at", None),
        purged_at=getattr(value, "purged_at", None),
    )


def _source(value: SourceModel) -> Source:
    return Source(
        id=value.id,
        code=value.code,
        name=value.name,
        trust_tier=value.trust_tier,
        feed_url=value.feed_url,
        allowed_domains=list(value.allowed_domains or []),
        status=value.status,
        adapter_type=value.adapter_type,
        rate_limit_per_minute=value.rate_limit_per_minute,
        crawl_interval_seconds=getattr(value, "crawl_interval_seconds", 3600) or 3600,
        license=getattr(value, "license", None) or "inherit",
        extra_config=value.extra_config or {},
        cursor=value.cursor,
        etag=value.etag,
        last_modified=value.last_modified,
        last_success_at=value.last_success_at,
        consecutive_failures=value.consecutive_failures,
        next_retry_at=value.next_retry_at,
        last_error_code=value.last_error_code,
    )


def _ingest_run(value: IngestRunModel) -> IngestRun:
    return IngestRun(
        id=value.id,
        source_id=value.source_id,
        trigger=value.trigger,
        started_at=value.started_at,
        status=value.status,
        finished_at=value.finished_at,
        fetched=value.fetched,
        processed=value.processed,
        quarantined=value.quarantined,
        message=value.message,
        request_id=value.request_id,
    )


def _llm_provider(value: LlmProviderConfigModel) -> LlmProviderConfig:
    return LlmProviderConfig(
        id=value.id,
        code=value.code,
        display_name=value.display_name,
        protocol=value.protocol,
        base_url=value.base_url or "",
        api_key_encrypted=value.api_key_encrypted or "",
        model=value.model,
        status=value.status,
        is_default=bool(value.is_default),
        timeout_seconds=float(value.timeout_seconds),
        max_tokens=int(value.max_tokens),
        temperature=float(value.temperature),
        extra_config=value.extra_config or {},
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _llm_binding(value: LlmAgentBindingModel) -> LlmAgentBinding:
    return LlmAgentBinding(
        agent_key=value.agent_key,
        provider_id=value.provider_id,
        model_override=value.model_override,
        updated_at=value.updated_at,
    )


def _user(value: UserModel) -> User:
    return User(
        id=value.id,
        username=value.username,
        password_hash=value.password_hash,
        role=value.role,
        status=value.status,
        created_at=value.created_at,
    )


def _audit_log(value: AuditLogModel) -> AuditLog:
    return AuditLog(
        id=value.id,
        actor_id=value.actor_id,
        action=value.action,
        object_type=value.object_type,
        object_id=value.object_id,
        request_id=value.request_id,
        details=value.details or {},
        created_at=value.created_at,
    )


def _review_task(value: ReviewTaskModel) -> ReviewTask:
    return ReviewTask(
        id=value.id,
        object_type=value.object_type,
        object_id=value.object_id,
        reason_code=value.reason_code,
        allowed_decisions=value.allowed_decisions,
        status=value.status,
        decision=value.decision,
        reviewer_id=value.reviewer_id,
        comment=value.comment,
        resume_from=value.resume_from,
        blackboard_version=value.blackboard_version,
        created_at=value.created_at,
        decided_at=value.decided_at,
    )


def _auto_review_attempt(value: AutoReviewAttemptModel) -> AutoReviewAttempt:
    return AutoReviewAttempt(
        id=value.id,
        task_id=value.task_id,
        object_type=value.object_type,
        object_id=value.object_id,
        status=value.status,
        decision=value.decision,
        confidence=value.confidence,
        reason=value.reason,
        model_run_id=value.model_run_id,
        created_at=value.created_at,
        context=getattr(value, "context", None) or {},
    )


def _model_run(value: ModelRunModel) -> ModelRun:
    return ModelRun(
        id=value.id,
        operation=value.operation,
        provider=value.provider,
        model=value.model,
        input_schema_version=value.input_schema_version,
        output_schema_version=value.output_schema_version,
        request_hash=value.request_hash,
        input_payload=value.input_payload,
        output_payload=value.output_payload,
        status=value.status,
        latency_ms=value.latency_ms,
        estimated_cost_usd=float(value.estimated_cost_usd),
        error_code=value.error_code,
        created_at=value.created_at,
    )


def _workflow_run(value: WorkflowRunModel) -> WorkflowRun:
    return WorkflowRun(
        id=value.id,
        event_id=value.event_id,
        trigger_id=value.trigger_id,
        status=value.status,
        as_of=value.as_of,
        current_node=value.current_node,
        state_version=value.state_version,
        blackboard=value.blackboard or {},
        error_code=value.error_code,
        budget_profile=getattr(value, "budget_profile", None) or "mvp_standard",
        created_at=value.created_at,
    )


def _artifact(value: ArtifactModel) -> Artifact:
    return Artifact(
        id=value.id,
        sha256=value.sha256,
        storage_uri=value.storage_uri,
        mime_type=value.mime_type,
        byte_size=value.byte_size,
        created_at=value.created_at,
    )


def _revision(value: DocumentRevisionModel) -> DocumentRevision:
    return DocumentRevision(
        id=value.id,
        document_id=value.document_id,
        revision_no=value.revision_no,
        artifact_id=value.artifact_id or "",
        content_hash=value.content_hash,
        normalized_content_uri=value.normalized_content_uri or "",
        parser_version=value.parser_version,
        created_at=value.created_at,
    )


def _entity(value: EntityModel) -> Entity:
    return Entity(
        id=value.id,
        entity_type=value.entity_type,
        canonical_name=value.canonical_name,
        status=value.status,
        valid_from=value.valid_from,
        valid_to=value.valid_to,
    )


def _security(value: SecurityModel) -> Security:
    return Security(
        id=value.id,
        entity_id=value.entity_id,
        ticker=value.ticker,
        exchange=value.exchange,
        market_code=value.market_code,
        valid_from=value.valid_from,
        valid_to=value.valid_to,
    )


def _parsed_document(value: ParsedDocumentModel) -> ParsedDocument:
    return ParsedDocument(
        id=value.id,
        document_id=value.document_id,
        revision_id=value.revision_id,
        parser_version=value.parser_version,
        parser_run_id=value.parser_run_id,
        language=value.language,
        title=value.title,
        block_ids=value.block_ids if value.block_ids is not None else [],
        summary=value.summary,
        created_at=value.created_at,
    )


def _document_block(value: DocumentBlockModel) -> DocumentBlock:
    return DocumentBlock(
        id=value.id,
        parsed_document_id=value.parsed_document_id,
        revision_id=value.revision_id,
        block_type=value.block_type,
        block_id=value.block_id,
        text=value.text,
        char_start=value.char_start,
        char_end=value.char_end,
        order_index=value.order_index,
        dom_path=value.dom_path,
        page_no=value.page_no,
        metadata=value.metadata_json if value.metadata_json is not None else {},
        created_at=value.created_at,
    )


def _document_chunk(value: DocumentChunkModel) -> DocumentChunk:
    return DocumentChunk(
        id=value.id,
        block_id=value.block_id,
        chunk_type=value.chunk_type,
        text=value.text,
        char_start=value.char_start,
        char_end=value.char_end,
        content_hash=value.content_hash,
        embedding_model_version=value.embedding_model_version or "",
        embedding=value.embedding,
        as_of=value.as_of,
        created_at=value.created_at,
    )


def _disclosure_group(value: DisclosureGroupModel) -> DisclosureGroup:
    return DisclosureGroup(
        id=value.id,
        canonical_content_hash=value.canonical_content_hash,
        canonical_document_id=value.canonical_document_id,
        entity_ids=value.entity_ids if value.entity_ids is not None else [],
        event_type_hints=value.event_type_hints if value.event_type_hints is not None else [],
        representative_embedding=value.representative_embedding,
        embedding_model_version=value.embedding_model_version,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _embedding_record(value: EmbeddingRecordModel) -> EmbeddingRecord:
    return EmbeddingRecord(
        id=value.id,
        chunk_id=value.chunk_id,
        embedding_model_version=value.embedding_model_version,
        embedding=value.embedding,
        content_hash=value.content_hash,
        status=value.status,
        error_code=value.error_code,
        created_at=value.created_at,
    )


def _disclosure_group_membership(
    value: DisclosureGroupMembershipModel,
) -> DisclosureGroupMembership:
    return DisclosureGroupMembership(
        id=value.id,
        disclosure_group_id=value.disclosure_group_id,
        document_id=value.document_id,
        source_tier=value.source_tier,
        reason=value.reason,
        joined_at=value.joined_at,
    )


def _event(value: EventModel) -> Event:
    return Event(
        id=value.id,
        event_type=value.event_type,
        status=value.status,
        title=value.title,
        entity_ids=value.entity_ids,
        document_ids=value.document_ids,
        disclosure_group_id=value.disclosure_group_id,
        importance=float(value.importance),
        urgency=value.urgency,
        occurred_at=value.occurred_at,
        version=value.version,
        key_fields=value.key_fields if value.key_fields is not None else {},
        confidence=float(value.confidence) if value.confidence is not None else 0.0,
        classifier_version=value.classifier_version or "",
        missing_required=value.missing_required if value.missing_required is not None else [],
        time_resolution=value.time_resolution if value.time_resolution is not None else {},
        capability_pack_id=getattr(value, "capability_pack_id", None),
        capability_pack_version=getattr(value, "capability_pack_version", None),
    )


def _evidence(value: EvidenceSpanModel) -> EvidenceSpan:
    return EvidenceSpan(
        id=value.id,
        document_id=value.document_id,
        revision_id=value.revision_id,
        locator=value.locator,
        excerpt=value.excerpt,
        excerpt_hash=value.excerpt_hash,
        locator_type=value.locator_type,
        extraction_method=value.extraction_method,
        extraction_version=value.extraction_version,
        created_at=value.created_at,
        deleted_at=getattr(value, "deleted_at", None),
    )


def _claim(value: ClaimModel) -> Claim:
    return Claim(
        id=value.id,
        event_id=value.event_id,
        subject_text=value.subject_text,
        predicate=value.predicate,
        object_value=value.object_value,
        status=value.status,
        confidence=float(value.confidence),
        evidence_ids=value.evidence_ids,
        as_of=value.as_of,
        subject_entity_id=value.subject_entity_id,
        qualifiers=value.qualifiers if value.qualifiers is not None else {},
        fingerprint=value.fingerprint,
        policy_version=value.policy_version,
    )


def _conflict(value: ConflictModel) -> ConflictRecord:
    return ConflictRecord(
        id=value.id,
        event_id=value.event_id,
        conflict_type=value.conflict_type,
        severity=value.severity,
        status=value.status,
        summary=value.summary,
        claim_ids=value.claim_ids,
        resolution=value.resolution,
        version=value.version,
    )


def _merge_review_task(value: MergeReviewTaskModel) -> MergeReviewTask:
    return MergeReviewTask(
        id=value.id,
        document_id=value.document_id,
        candidates=value.candidates,
        status=value.status,
        decision=value.decision,
        reviewer_id=value.reviewer_id,
        decided_at=value.decided_at,
        created_at=value.created_at,
    )


def _watch_trigger(value: WatchTriggerModel) -> WatchTrigger:
    return WatchTrigger(
        id=value.id,
        event_id=value.event_id,
        trigger_type=value.trigger_type,
        condition=value.condition if value.condition else {},
        status=value.status,
        created_at=value.created_at,
        fired_at=value.fired_at,
    )


def _event_type_registry(value: EventTypeRegistryModel) -> EventTypeRegistryEntry:
    return EventTypeRegistryEntry(
        type_label=value.type_label,
        status=value.status,
        event_count=value.event_count,
        decided_by=value.decided_by,
        decided_at=value.decided_at,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _ood_observation(value: OODObservationModel) -> OODObservation:
    return OODObservation(
        id=value.id,
        event_id=value.event_id,
        document_id=value.document_id,
        status=value.status,
        ood_score=float(value.ood_score or 0),
        financial_relevance=float(value.financial_relevance or 0),
        closest_known_types=value.closest_known_types or [],
        extracted_features=value.extracted_features or {},
        evidence_ids=value.evidence_ids or [],
        classifier_version=value.classifier_version,
        router_version=value.router_version,
        embedding_model_version=value.embedding_model_version,
        generic_pack_id=value.generic_pack_id,
        generic_pack_version=value.generic_pack_version,
        cluster_id=value.cluster_id,
        observed_at=value.observed_at,
        as_of=value.as_of,
        version=value.version,
    )


def _ood_cluster(value: OODClusterModel) -> OODCluster:
    return OODCluster(
        id=value.id,
        label=value.label,
        status=value.status,
        member_count=value.member_count,
        independent_source_count=value.independent_source_count,
        cohesion_score=float(value.cohesion_score or 0),
        separation_score=float(value.separation_score or 0),
        stability_score=float(value.stability_score or 0),
        first_seen_at=value.first_seen_at,
        last_seen_at=value.last_seen_at,
        cluster_version=value.cluster_version,
    )


def _ood_feature_snapshot(value: OODFeatureSnapshotModel) -> OODFeatureSnapshot:
    return OODFeatureSnapshot(
        id=value.id,
        observation_id=value.observation_id,
        feature_schema_version=value.feature_schema_version,
        features=value.features or {},
        generated_at=value.generated_at,
    )


def _event_type_proposal(value: EventTypeProposalModel) -> EventTypeProposal:
    return EventTypeProposal(
        id=value.id,
        cluster_id=value.cluster_id,
        proposed_label=value.proposed_label,
        display_name=value.display_name,
        definition=value.definition,
        status=value.status,
        parent_type=value.parent_type,
        inclusion_rules=value.inclusion_rules or [],
        exclusion_rules=value.exclusion_rules or [],
        required_fields=value.required_fields or [],
        optional_fields=value.optional_fields or [],
        mechanisms=value.mechanisms or [],
        representative_event_ids=value.representative_event_ids or [],
        counterexample_event_ids=value.counterexample_event_ids or [],
        confidence=float(value.confidence or 0),
        agent_run_id=value.agent_run_id,
        created_at=value.created_at,
        decided_at=value.decided_at,
    )


def _capability_evaluation(value: CapabilityEvaluationModel) -> CapabilityEvaluation:
    return CapabilityEvaluation(
        id=value.id,
        pack_id=value.pack_id,
        pack_version=value.pack_version,
        baseline_pack_id=value.baseline_pack_id,
        baseline_pack_version=value.baseline_pack_version,
        status=value.status,
        metrics=value.metrics or {},
        comparison=value.comparison or {},
        recommendation=value.recommendation,
        created_at=value.created_at,
    )


def _reprocessing_job(value: ReprocessingJobModel) -> ReprocessingJob:
    return ReprocessingJob(
        id=value.id,
        source_pack_id=value.source_pack_id,
        target_pack_id=value.target_pack_id,
        event_ids=value.event_ids or [],
        status=value.status,
        total_count=value.total_count,
        success_count=value.success_count,
        failed_count=value.failed_count,
        summary=value.summary or {},
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _fact_card(value: FactCardModel) -> FactCard:
    return FactCard(
        id=value.id,
        event_id=value.event_id,
        version=value.version,
        status=value.status,
        title=value.title,
        summary=value.summary,
        claim_ids=value.claim_ids,
        as_of=value.as_of,
        report_type=value.report_type,
        disclaimer=value.disclaimer,
        supersedes_report_id=value.supersedes_report_id,
        change_reason=value.change_reason,
        content=value.content or {},
        provenance=value.provenance or {},
    )


def _impact_analysis(value: ImpactAnalysisModel) -> ImpactAnalysis:
    return ImpactAnalysis(
        id=value.id,
        event_id=value.event_id,
        version=value.version,
        status=value.status,
        event_title_snapshot=value.event_title_snapshot,
        summary=value.summary,
        transmission_chains=value.transmission_chains or [],
        impacts=value.impacts or [],
        macro_assumptions=value.macro_assumptions or [],
        watch_items=value.watch_items or [],
        generated_by=value.generated_by,
        model_run_id=value.model_run_id,
        degraded=value.degraded or False,
        supersedes_id=value.supersedes_id,
        created_at=value.created_at,
        analysis_payload=value.analysis_payload or {},
        quality_report=value.quality_report or {},
        edit_revision=value.edit_revision or 0,
        derived_from_id=value.derived_from_id,
        preliminary_assessment_id=getattr(value, "preliminary_assessment_id", None),
    )


def _preliminary_assessment(value: EventPreliminaryAssessmentModel) -> EventPreliminaryAssessment:
    return EventPreliminaryAssessment(
        id=value.id,
        event_id=value.event_id,
        workflow_id=value.workflow_id,
        version=value.version,
        status=value.status,
        event_title_snapshot=value.event_title_snapshot,
        as_of=value.as_of,
        summary=value.summary,
        thesis=value.thesis,
        direction=value.direction,
        significance=value.significance,
        confidence=float(value.confidence),
        assessment_payload=value.assessment_payload or {},
        input_snapshot=value.input_snapshot or {},
        input_hash=value.input_hash,
        quality_report=value.quality_report or {},
        generated_by=value.generated_by,
        model_run_id=value.model_run_id,
        agent_version=value.agent_version,
        prompt_version=value.prompt_version,
        supersedes_id=value.supersedes_id,
        created_at=value.created_at,
    )


def _impact_graph_layout(value: ImpactGraphLayoutModel) -> ImpactGraphLayout:
    return ImpactGraphLayout(
        analysis_id=value.analysis_id,
        user_id=value.user_id,
        node_positions=value.node_positions or {},
        collapsed_groups=value.collapsed_groups or [],
        viewport=value.viewport or {},
        updated_at=value.updated_at,
    )


def _impact_target(value: ImpactTargetDefinitionModel) -> ImpactTargetDefinition:
    return ImpactTargetDefinition(
        id=value.id,
        target_type=value.target_type,
        target_code=value.target_code,
        canonical_name=value.canonical_name,
        taxonomy_version=value.taxonomy_version,
        aliases=value.aliases or [],
        parent_target_id=getattr(value, "parent_target_id", None),
        hierarchy_level=getattr(value, "hierarchy_level", 0),
        hierarchy_status=getattr(value, "hierarchy_status", "approved"),
        hierarchy_source=getattr(value, "hierarchy_source", "manual"),
        propagation_weight=getattr(value, "propagation_weight", 0.85),
        reviewed_by=getattr(value, "reviewed_by", None),
        reviewed_at=getattr(value, "reviewed_at", None),
        valid_from=value.valid_from,
        valid_to=value.valid_to,
    )


def _market_instrument(value: MarketInstrumentModel) -> MarketInstrument:
    return MarketInstrument(
        id=value.id,
        market=value.market,
        symbol=value.symbol,
        name=value.name,
        instrument_type=value.instrument_type,
        exchange=value.exchange,
        currency=value.currency,
        timezone=value.timezone,
        sector_code=value.sector_code,
        sector_name=value.sector_name,
        active=value.active,
        valid_from=value.valid_from,
        valid_to=value.valid_to,
        provider_symbols=value.provider_symbols or {},
    )


def _industry_taxonomy(value: IndustryTaxonomyModel) -> IndustryTaxonomy:
    return IndustryTaxonomy(
        **{key: getattr(value, key) for key in IndustryTaxonomy.__dataclass_fields__}
    )


def _industry_classification(value: IndustryClassificationModel) -> IndustryClassification:
    return IndustryClassification(
        **{key: getattr(value, key) for key in IndustryClassification.__dataclass_fields__}
    )


def _instrument_industry_membership(
    value: InstrumentIndustryMembershipModel,
) -> InstrumentIndustryMembership:
    return InstrumentIndustryMembership(
        **{key: getattr(value, key) for key in InstrumentIndustryMembership.__dataclass_fields__}
    )


def _impact_target_mapping(value: ImpactTargetMappingModel) -> ImpactTargetMapping:
    return ImpactTargetMapping(
        **{key: getattr(value, key) for key in ImpactTargetMapping.__dataclass_fields__}
    )


def _market_master_data_import_run(
    value: MarketMasterDataImportRunModel,
) -> MarketMasterDataImportRun:
    return MarketMasterDataImportRun(
        **{key: getattr(value, key) for key in MarketMasterDataImportRun.__dataclass_fields__}
    )


def _event_impact_relation(value: EventImpactRelationModel) -> EventImpactRelation:
    return EventImpactRelation(
        id=value.id,
        source_event_id=value.source_event_id,
        target_event_id=value.target_event_id,
        relation_type=value.relation_type,
        dependency_weight=value.dependency_weight,
        confidence=value.confidence,
        evidence_refs=value.evidence_refs or [],
        status=value.status,
        created_at=value.created_at,
    )


def _impact_contribution(value: ImpactContributionModel) -> ImpactContribution:
    return ImpactContribution(
        id=value.id,
        event_id=value.event_id,
        analysis_id=value.analysis_id,
        assessment_id=value.assessment_id,
        target_id=value.target_id,
        scenario_id=value.scenario_id,
        direction=value.direction,
        magnitude=value.magnitude,
        horizon=value.horizon,
        base_strength=value.base_strength,
        effective_strength=value.effective_strength,
        event_importance=value.event_importance,
        assessment_confidence=value.assessment_confidence,
        path_confidence=value.path_confidence,
        dependency_weight=value.dependency_weight,
        target_role=getattr(value, "target_role", "direct_subject"),
        relationship_id=getattr(value, "relationship_id", None),
        relationship_confidence=getattr(value, "relationship_confidence", 1.0),
        inference_kind=getattr(value, "inference_kind", "derived"),
        evidence_refs=getattr(value, "evidence_refs", None) or [],
        conditions=getattr(value, "conditions", None) or [],
        invalidation_conditions=getattr(value, "invalidation_conditions", None) or [],
        publication_scope=getattr(value, "publication_scope", "official"),
        valid_from=value.valid_from,
        expected_peak_at=value.expected_peak_at,
        valid_to=value.valid_to,
        rule_version=value.rule_version,
        created_at=value.created_at,
    )


def _impact_dimension_contribution(
    value: ImpactDimensionContributionModel,
) -> ImpactDimensionContribution:
    return ImpactDimensionContribution(
        id=value.id,
        contribution_id=value.contribution_id,
        dimension=value.dimension,
        direction=value.direction,
        magnitude=value.magnitude,
        base_strength=value.base_strength,
        effective_strength=value.effective_strength,
        confidence=value.confidence,
        quantitative_range=value.quantitative_range,
        unit=value.unit,
        evidence_refs=value.evidence_refs or [],
    )


def _target_impact_snapshot(value: TargetImpactSnapshotModel) -> TargetImpactSnapshot:
    return TargetImpactSnapshot(
        id=value.id,
        target_id=value.target_id,
        as_of=value.as_of,
        horizon=value.horizon,
        scenario_set_id=value.scenario_set_id,
        positive_gross=value.positive_gross,
        negative_gross=value.negative_gross,
        net_score=value.net_score,
        direction=value.direction,
        magnitude=value.magnitude,
        confidence=value.confidence,
        dominant_event_id=value.dominant_event_id,
        previous_direction=value.previous_direction,
        change_type=value.change_type,
        source_hash=value.source_hash,
        rule_version=value.rule_version,
        explanation=value.explanation or "",
        created_at=value.created_at,
    )


def _target_impact_snapshot_contribution(
    value: TargetImpactSnapshotContributionModel,
) -> TargetImpactSnapshotContribution:
    return TargetImpactSnapshotContribution(
        snapshot_id=value.snapshot_id,
        contribution_id=value.contribution_id,
        event_id=value.event_id,
        direction=value.direction,
        effective_strength=value.effective_strength,
        contribution_share=value.contribution_share,
    )


def _market_forecast_run(value: MarketForecastRunModel) -> MarketForecastRun:
    return MarketForecastRun(
        id=value.id,
        instrument_id=value.instrument_id,
        as_of=value.as_of,
        horizon=value.horizon,
        direction=value.direction,
        probabilities=value.probabilities,
        expected_return_p10=value.expected_return_p10,
        expected_return_p50=value.expected_return_p50,
        expected_return_p90=value.expected_return_p90,
        confidence=value.confidence,
        forecast_status=value.forecast_status,
        data_status=value.data_status,
        calibration_version_id=value.calibration_version_id,
        rule_version=value.rule_version,
        factor_rule_version=value.factor_rule_version,
        factor_source_hash=value.factor_source_hash,
        source_hash=value.source_hash,
        input_snapshot=value.input_snapshot or {},
        created_by=value.created_by,
        created_at=value.created_at,
    )


def _market_forecast_outcome(value: MarketForecastOutcomeModel) -> MarketForecastOutcome:
    return MarketForecastOutcome(
        id=value.id,
        forecast_id=value.forecast_id,
        outcome_observed_at=value.outcome_observed_at,
        realized_return=value.realized_return,
        outcome=value.outcome,
        base_price=value.base_price,
        outcome_price=value.outcome_price,
        source=value.source,
        available_at=value.available_at,
        label_rule_version=value.label_rule_version,
        created_at=value.created_at,
    )


def _market_calibration_version(
    value: MarketCalibrationVersionModel,
) -> MarketCalibrationVersion:
    return MarketCalibrationVersion(
        id=value.id,
        model_key=value.model_key,
        version=value.version,
        horizon=value.horizon,
        market=value.market,
        status=value.status,
        method=value.method,
        parameters=value.parameters or {},
        metrics=value.metrics or {},
        train_start=value.train_start,
        train_end=value.train_end,
        sample_count=value.sample_count,
        created_by=value.created_by,
        created_at=value.created_at,
        published_at=value.published_at,
    )


def _forward_impact_window(value: ForwardImpactWindowModel) -> ForwardImpactWindow:
    return ForwardImpactWindow(
        id=value.id,
        target_id=value.target_id,
        as_of=value.as_of,
        window_start=value.window_start,
        window_end=value.window_end,
        event_types=value.event_types or [],
        catalyst_ids=value.catalyst_ids or [],
        included_kinds=value.included_kinds or [],
        granularity=value.granularity,
        scenario_set_id=value.scenario_set_id,
        status=value.status,
        rule_version=value.rule_version,
        source_hash=value.source_hash,
        created_by=value.created_by,
        created_at=value.created_at,
    )


def _forward_catalyst(value: ForwardCatalystModel) -> ForwardCatalyst:
    return ForwardCatalyst(
        id=value.id,
        target_id=value.target_id,
        kind=value.kind,
        title=value.title,
        event_type=value.event_type,
        scheduled_from=value.scheduled_from,
        scheduled_to=value.scheduled_to,
        trigger_definition=value.trigger_definition or {},
        probability_low=value.probability_low,
        probability_base=value.probability_base,
        probability_high=value.probability_high,
        probability_basis=value.probability_basis,
        evidence_refs=value.evidence_refs or [],
        status=value.status,
        realized_event_id=value.realized_event_id,
        created_by=value.created_by,
        created_at=value.created_at,
    )


def _future_event(value: FutureEventModel) -> FutureEvent:
    return FutureEvent(
        id=value.id,
        event_type=value.event_type,
        kind=value.kind,
        series_key=value.series_key,
        external_id=value.external_id,
        source_id=value.source_id,
        current_revision_id=value.current_revision_id,
        realized_event_id=value.realized_event_id,
        created_by=value.created_by,
        created_at=value.created_at,
    )


def _future_event_revision(value: FutureEventRevisionModel) -> FutureEventRevision:
    return FutureEventRevision(
        id=value.id,
        future_event_id=value.future_event_id,
        revision_no=value.revision_no,
        title=value.title,
        description=value.description or "",
        scheduled_from=value.scheduled_from,
        scheduled_to=value.scheduled_to,
        source_timezone=value.source_timezone,
        time_precision=value.time_precision,
        status=value.status,
        importance=value.importance,
        probability_low=value.probability_low,
        probability_base=value.probability_base,
        probability_high=value.probability_high,
        probability_basis=value.probability_basis,
        source_url=value.source_url,
        evidence_refs=value.evidence_refs or [],
        available_at=value.available_at,
        change_reason=value.change_reason or "",
        supersedes_revision_id=value.supersedes_revision_id,
        created_by=value.created_by,
        created_at=value.created_at,
    )


def _future_event_target_impact(
    value: FutureEventTargetImpactModel,
) -> FutureEventTargetImpact:
    return FutureEventTargetImpact(
        id=value.id,
        future_event_id=value.future_event_id,
        revision_id=value.revision_id,
        target_id=value.target_id,
        scenario_id=value.scenario_id,
        direction=value.direction,
        magnitude=value.magnitude,
        conditional_strength=value.conditional_strength,
        occurrence_probability=value.occurrence_probability,
        expected_strength=value.expected_strength,
        confidence=value.confidence,
        rationale=value.rationale or "",
        onset_at=value.onset_at,
        expected_peak_at=value.expected_peak_at,
        valid_to=value.valid_to,
        causal_edge_refs=value.causal_edge_refs or [],
        evidence_refs=value.evidence_refs or [],
        status=value.status,
        created_at=value.created_at,
    )


def _forward_impact_contribution(
    value: ForwardImpactContributionModel,
) -> ForwardImpactContribution:
    return ForwardImpactContribution(
        id=value.id,
        window_id=value.window_id,
        catalyst_id=value.catalyst_id,
        target_id=value.target_id,
        scenario_id=value.scenario_id,
        direction=value.direction,
        magnitude=value.magnitude,
        conditional_strength=value.conditional_strength,
        occurrence_probability=value.occurrence_probability,
        expected_strength=value.expected_strength,
        confidence=value.confidence,
        onset_at=value.onset_at,
        expected_peak_at=value.expected_peak_at,
        valid_to=value.valid_to,
        causal_edge_refs=value.causal_edge_refs or [],
        created_at=value.created_at,
    )


def _forward_impact_point(value: ForwardImpactPointModel) -> ForwardImpactPoint:
    return ForwardImpactPoint(
        id=value.id,
        window_id=value.window_id,
        point_at=value.point_at,
        scenario_id=value.scenario_id,
        positive_conditional=value.positive_conditional,
        negative_conditional=value.negative_conditional,
        net_conditional=value.net_conditional,
        positive_expected=value.positive_expected,
        negative_expected=value.negative_expected,
        net_expected=value.net_expected,
        direction=value.direction,
        confidence=value.confidence,
        dominant_catalyst_id=value.dominant_catalyst_id,
    )


def _agent_registration(value: AgentRegistrationModel) -> AgentRegistration:
    return AgentRegistration(
        agent_key=value.agent_key,
        version=value.version,
        display_name=value.display_name,
        capabilities=value.capabilities or [],
        input_schema_refs=value.input_schema_refs or [],
        output_schema_ref=value.output_schema_ref,
        allowed_tools=value.allowed_tools or [],
        budget_profile=value.budget_profile,
        quality_gates=value.quality_gates or {},
        config=value.config or {},
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _research_task(value: ResearchTaskModel) -> ResearchTask:
    return ResearchTask(
        id=value.id,
        plan_id=value.plan_id,
        name=value.name,
        agent_key=value.agent_key,
        description=value.description,
        dependencies=value.dependencies or [],
        required=value.required,
        status=value.status,
        input_fields=value.input_fields or [],
        output_field=value.output_field,
        tool_strategy=value.tool_strategy or {},
        output_schema=value.output_schema,
        input_hash=value.input_hash,
        output_snapshot=value.output_snapshot,
        review_reason=value.review_reason,
        started_at=value.started_at,
        ended_at=value.ended_at,
        created_at=value.created_at,
    )


def _research_plan(value: ResearchPlanModel, tasks: list[ResearchTask]) -> ResearchPlan:
    return ResearchPlan(
        id=value.id,
        workflow_id=value.workflow_id,
        question=value.question,
        objective=value.objective,
        as_of=value.as_of,
        status=value.status,
        tasks=tasks,
        budget_profile=value.budget_profile,
        completion_criteria=value.completion_criteria or {},
        metadata=value.plan_metadata or {},
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _brief_entry_dict(entry: BriefEntry) -> dict:
    return {
        "report_id": entry.report_id,
        "event_id": entry.event_id,
        "entity_ids": list(entry.entity_ids),
        "title": entry.title,
        "importance": entry.importance,
        "urgency": entry.urgency,
        "confidence": entry.confidence,
        "novelty": entry.novelty,
        "recency": entry.recency,
        "score": entry.score,
        "rank": entry.rank,
    }


def _brief_entry(value: dict) -> BriefEntry:
    return BriefEntry(
        report_id=value["report_id"],
        event_id=value["event_id"],
        entity_ids=value.get("entity_ids", []),
        title=value["title"],
        importance=value["importance"],
        urgency=value["urgency"],
        confidence=value["confidence"],
        novelty=value["novelty"],
        recency=value["recency"],
        score=value["score"],
        rank=value["rank"],
    )


def _brief(value: BriefModel) -> Brief:
    return Brief(
        id=value.id,
        brief_date=value.brief_date,
        entries=[_brief_entry(e) for e in (value.entries or [])],
        candidate_count=value.candidate_count,
        rule_version=value.rule_version,
        generated_at=value.generated_at,
    )
