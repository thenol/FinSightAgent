from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.domain import LLM_AGENT_KEYS


class IngestDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    source_tier: str = Field(pattern="^[SABC]$")
    external_id: Optional[str] = None
    url: Optional[str] = None
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    published_at: datetime


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class SourceCreateRequest(BaseModel):
    code: str = Field(pattern="^[a-z0-9_-]+$")
    name: str = Field(min_length=1)
    trust_tier: str = Field(pattern="^[SABC]$")
    feed_url: str = Field(min_length=1)
    allowed_domains: list[str] = Field(min_length=1)
    adapter_type: str = Field(default="rss", pattern="^[a-z0-9_]+$")
    rate_limit_per_minute: int = Field(default=10, ge=1, le=600)
    crawl_interval_seconds: int = Field(default=3600, ge=60, le=86400)
    license: str = Field(default="inherit", pattern="^(inherit|full|excerpt|entry_only)$")
    extra_config: dict[str, Any] = Field(default_factory=dict)


class SourceUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    trust_tier: Optional[str] = Field(default=None, pattern="^[SABC]$")
    feed_url: Optional[str] = Field(default=None, min_length=1)
    allowed_domains: Optional[list[str]] = Field(default=None, min_length=1)
    rate_limit_per_minute: Optional[int] = Field(default=None, ge=1, le=600)
    crawl_interval_seconds: Optional[int] = Field(default=None, ge=60, le=86400)
    license: Optional[str] = Field(default=None, pattern="^(inherit|full|excerpt|entry_only)$")
    status: Optional[str] = Field(default=None, pattern="^(active|disabled|degraded)$")
    adapter_type: Optional[str] = Field(default=None, pattern="^[a-z0-9_]+$")
    extra_config: Optional[dict[str, Any]] = None


class SourceCollectionConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduler_enabled: Optional[bool] = None
    default_crawl_interval_seconds: Optional[int] = Field(default=None, ge=60, le=86400)
    max_concurrent_runs: Optional[int] = Field(default=None, ge=1, le=64)
    retry_limit: Optional[int] = Field(default=None, ge=0, le=10)


class DocumentUpdateRequest(BaseModel):
    retention_hold: Optional[bool] = None


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(pattern="^(researcher|reviewer|publisher|admin)$")


class UserUpdateRequest(BaseModel):
    role: Optional[str] = Field(default=None, pattern="^(researcher|reviewer|publisher|admin)$")
    status: Optional[str] = Field(default=None, pattern="^(active|disabled)$")


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: str
    username: str
    role: str
    status: str
    created_at: Optional[datetime]


class QuarantineItemResponse(BaseModel):
    id: str
    source_id: str
    external_id: Optional[str]
    url: Optional[str]
    error_code: str
    detail: Optional[str]
    attempts: int
    status: str
    created_at: Optional[datetime]


class OutboxMessageResponse(BaseModel):
    id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    trace_id: str
    attempts: int
    created_at: Optional[datetime]
    published_at: Optional[datetime]
    next_attempt_at: Optional[datetime]
    last_error: Optional[str]
    dead_lettered_at: Optional[datetime]


class ModelRunResponse(BaseModel):
    id: str
    operation: str
    provider: str
    model: str
    status: str
    latency_ms: int
    estimated_cost_usd: float
    error_code: Optional[str]
    created_at: Optional[datetime]


class ConflictResponse(BaseModel):
    id: str
    event_id: str
    conflict_type: str
    severity: str
    status: str
    summary: str
    claim_ids: list[str]
    resolution: Optional[str]
    version: int


class SourceResponse(BaseModel):
    id: str
    code: str
    name: str
    trust_tier: str
    feed_url: str
    allowed_domains: list[str]
    status: str
    adapter_type: str = "rss"
    rate_limit_per_minute: int = 10
    crawl_interval_seconds: int = 3600
    license: str = "inherit"
    extra_config: dict[str, Any] = Field(default_factory=dict)
    etag: Optional[str]
    last_modified: Optional[str]
    last_success_at: Optional[datetime]
    consecutive_failures: int
    next_retry_at: Optional[datetime]
    last_error_code: Optional[str]


class IngestRunResponse(BaseModel):
    id: str
    source_id: str
    trigger: str
    started_at: datetime
    finished_at: Optional[datetime]
    status: str
    fetched: int
    processed: int
    quarantined: int
    message: Optional[str]
    request_id: Optional[str]


class SourceHealthResponse(BaseModel):
    source: SourceResponse
    health: str = Field(description="healthy | degraded | disabled")
    consecutive_failures: int
    last_success_at: Optional[datetime]
    last_run: Optional[IngestRunResponse]
    recent_runs: list[IngestRunResponse]


class LlmProviderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern="^[a-z0-9_-]+$", max_length=64)
    display_name: str = Field(min_length=1, max_length=200)
    protocol: str = Field(pattern="^(openai_compatible|anthropic|deterministic)$")
    base_url: str = ""
    model: str = Field(min_length=1, max_length=120)
    api_key: str = ""
    is_default: bool = False
    timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    max_tokens: int = Field(default=2048, ge=1, le=128000)
    temperature: float = Field(default=0.2, ge=0, le=2)
    extra_config: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="active", pattern="^(active|disabled)$")


class LlmProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    base_url: Optional[str] = None
    model: Optional[str] = Field(default=None, min_length=1, max_length=120)
    status: Optional[str] = Field(default=None, pattern="^(active|disabled)$")
    is_default: Optional[bool] = None
    timeout_seconds: Optional[float] = Field(default=None, gt=0, le=120)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=128000)
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    extra_config: Optional[dict[str, Any]] = None


class LlmProviderRotateKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, max_length=4096)


class LlmAgentBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_key: str = Field(pattern="^(" + "|".join(sorted(LLM_AGENT_KEYS)) + ")$")
    provider_id: Optional[str] = None
    model_override: Optional[str] = Field(default=None, max_length=120)


class LlmAgentBindingBulkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: Optional[str] = None
    model_override: Optional[str] = Field(default=None, max_length=120)


class AgentRuntimeConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    timeout_seconds: Optional[float] = Field(default=None, gt=0, le=120)


class AgentPromptVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str = Field(min_length=1, max_length=12000)
    instruction_appendix: str = Field(default="", max_length=4000)
    change_note: str = Field(min_length=1, max_length=500)


class SystemOutboxRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outbox_ids: list[str] = Field(default_factory=list, max_length=100)
    retry_all_dead: bool = False


class ReviewPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(pattern="^(agent|human)$")


class ReviewPolicyResponse(BaseModel):
    id: str
    mode: str
    min_confidence: float
    source: str
    updated_by: Optional[str]
    updated_at: Optional[datetime]
    emergency_disabled: bool


class AuditLogResponse(BaseModel):
    id: str
    actor_id: Optional[str]
    action: str
    object_type: str
    object_id: Optional[str]
    request_id: Optional[str]
    details: dict[str, Any]
    created_at: Optional[datetime]


class ReportTransitionRequest(BaseModel):
    status: str = Field(pattern="^(approved|published|withdrawn|needs_review|needs_revision)$")


class ReviewDecisionRequest(BaseModel):
    decision: str = Field(
        pattern="^(approve|return|return_for_supplement|downgrade_to_fact_card|reject)$"
    )
    comment: str = Field(min_length=1, max_length=2000)
    resume_from: Optional[str] = Field(default=None, max_length=80)
    budget_adjust: Optional[dict[str, int]] = None


class ReviewTaskResponse(BaseModel):
    id: str
    object_type: str
    object_id: str
    reason_code: str
    allowed_decisions: list[str]
    status: str
    decision: Optional[str]
    reviewer_id: Optional[str]
    comment: Optional[str]
    resume_from: Optional[str] = None
    blackboard_version: Optional[int] = None
    created_at: Optional[datetime]
    decided_at: Optional[datetime]


class MergeReviewTaskResponse(BaseModel):
    id: str
    document_id: str
    candidates: list[str]
    status: str
    decision: Optional[str]
    reviewer_id: Optional[str]
    decided_at: Optional[datetime]
    created_at: Optional[datetime]


class MergeReviewDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(merge|new_event|skip)$")
    comment: str = Field(min_length=1, max_length=2000)


class WorkflowCreateRequest(BaseModel):
    trigger_id: str = Field(default="manual", min_length=1, max_length=80)
    as_of: Optional[datetime] = None
    # 默认同步执行图，避免依赖本机 workflow worker；异步入队可传 false
    execute: bool = True


class ResearchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    as_of: Optional[datetime] = None
    event_id: Optional[str] = Field(default=None, min_length=1)
    budget_profile: str = Field(default="mvp_standard", pattern="^(mvp_standard|mvp_low)$")
    execute: bool = True


class ResearchTaskResponse(BaseModel):
    id: str
    plan_id: str
    name: str
    agent_key: str
    description: str
    dependencies: list[str]
    required: bool
    status: str
    input_fields: list[str]
    output_field: Optional[str]
    output_schema: Optional[str]
    output_snapshot: Optional[dict[str, Any]]
    review_reason: Optional[str]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    created_at: Optional[datetime]


class ResearchPlanResponse(BaseModel):
    id: str
    workflow_id: str
    question: str
    objective: str
    as_of: datetime
    status: str
    budget_profile: str
    completion_criteria: dict[str, Any]
    metadata: dict[str, Any]
    tasks: list[ResearchTaskResponse]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class ResearchPlanListResponse(BaseModel):
    id: str
    workflow_id: str
    question: str
    objective: str
    as_of: datetime
    status: str
    budget_profile: str
    metadata: dict[str, Any]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class ResearchBlackboardResponse(BaseModel):
    workflow_id: str
    research_plan: dict[str, Any]
    task_outputs: dict[str, Any]


class WorkflowResumeRequest(BaseModel):
    trigger: str = Field(
        default="budget_resume",
        pattern="^(budget_resume|downgrade_fact_only|company_returned|new_evidence)$",
    )
    resume_from: Optional[str] = Field(default=None, max_length=80)
    budget_adjust: Optional[dict[str, int]] = None
    force_fact_only: bool = False
    reason: str = Field(default="admin_resume", min_length=1, max_length=2000)


class WorkflowResponse(BaseModel):
    id: str
    event_id: str
    trigger_id: str
    status: str
    as_of: datetime
    current_node: Optional[str]
    state_version: int
    blackboard: dict[str, Any]
    error_code: Optional[str]
    budget_profile: str = "mvp_standard"
    created_at: Optional[datetime] = None
    display: dict[str, Any] = Field(default_factory=dict)
    technical: dict[str, Any] = Field(default_factory=dict)


class BudgetLedgerEntryResponse(BaseModel):
    id: str
    workflow_id: str
    node_name: Optional[str]
    dimension: str
    entry_type: str
    amount: int
    created_at: Optional[datetime] = None


class NodeAttemptResponse(BaseModel):
    id: str
    workflow_id: str
    node_name: str
    attempt_no: int
    input_hash: str
    status: str
    error_code: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class EvidenceResponse(BaseModel):
    id: str
    document_id: str
    revision_id: str
    locator: dict[str, Any]
    excerpt: str
    excerpt_hash: str
    locator_type: str
    extraction_method: str
    extraction_version: str
    created_at: datetime
    document_title: Optional[str] = None
    document_url: Optional[str] = None
    document_content: Optional[str] = None
    display_scope: Optional[str] = None


class PipelineResponse(BaseModel):
    status: str
    document_id: str
    event_id: str
    evidence_id: str
    claim_id: str
    claim_status: str
    fact_card_id: str
    report_status: str


class EventResponse(BaseModel):
    id: str
    event_type: str
    status: str
    title: str
    entity_ids: list[str]
    document_ids: list[str]
    importance: float
    urgency: str
    occurred_at: datetime
    version: int
    confidence: float = 0.0
    key_fields: dict[str, Any] = Field(default_factory=dict)
    missing_required: list[str] = Field(default_factory=list)
    capability_pack_id: Optional[str] = None
    capability_pack_version: Optional[str] = None


class OverviewEventImpactResponse(BaseModel):
    event: EventResponse
    analysis_status: str
    direction: str
    positive_strength: float = 0.0
    negative_strength: float = 0.0
    confidence: float = 0.0
    horizon: Optional[str] = None
    affected_targets: list[dict[str, Any]] = Field(default_factory=list)
    explanation: str = ""


class OverviewTargetImpactResponse(BaseModel):
    target_id: str
    target_type: str
    target_code: str
    canonical_name: str
    direction: str
    net_score: float
    confidence: float
    event_count: int


class ResearchOverviewResponse(BaseModel):
    as_of: datetime
    window: str
    publication_scope: str
    rule_version: str
    summary: dict[str, Any] = Field(default_factory=dict)
    events: list[OverviewEventImpactResponse] = Field(default_factory=list)
    targets: list[OverviewTargetImpactResponse] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    data_quality: dict[str, Any] = Field(default_factory=dict)


class EventTypeRegistryResponse(BaseModel):
    type_label: str
    status: str
    event_count: int
    promotion_ready: bool = False
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class OODObservationResponse(BaseModel):
    id: str
    event_id: str
    document_id: str
    status: str
    ood_score: float
    financial_relevance: float
    closest_known_types: list[dict[str, Any]] = Field(default_factory=list)
    extracted_features: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    classifier_version: str
    router_version: str
    embedding_model_version: Optional[str] = None
    generic_pack_id: Optional[str] = None
    generic_pack_version: Optional[str] = None
    cluster_id: Optional[str] = None
    observed_at: Optional[datetime] = None
    as_of: Optional[datetime] = None
    version: int = 1


class OODClusterResponse(BaseModel):
    id: str
    label: str
    status: str
    member_count: int
    independent_source_count: int
    cohesion_score: float
    separation_score: float
    stability_score: float
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    cluster_version: int


class EventTypeProposalResponse(BaseModel):
    id: str
    cluster_id: str
    proposed_label: str
    display_name: str
    definition: str
    status: str
    parent_type: Optional[str] = None
    inclusion_rules: list[str] = Field(default_factory=list)
    exclusion_rules: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    optional_fields: list[str] = Field(default_factory=list)
    mechanisms: list[dict[str, Any]] = Field(default_factory=list)
    representative_event_ids: list[str] = Field(default_factory=list)
    counterexample_event_ids: list[str] = Field(default_factory=list)
    confidence: float
    agent_run_id: Optional[str] = None
    created_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None


class CapabilityEvaluationResponse(BaseModel):
    id: str
    pack_id: str
    pack_version: str
    baseline_pack_id: Optional[str] = None
    baseline_pack_version: Optional[str] = None
    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    comparison: dict[str, Any] = Field(default_factory=dict)
    recommendation: str
    created_at: Optional[datetime] = None


class ReprocessingJobResponse(BaseModel):
    id: str
    source_pack_id: Optional[str] = None
    target_pack_id: str
    event_ids: list[str] = Field(default_factory=list)
    status: str
    total_count: int
    success_count: int
    failed_count: int
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ClaimResponse(BaseModel):
    id: str
    subject_text: str
    predicate: str
    object_value: dict[str, Any]
    status: str
    confidence: float
    evidence_ids: list[str]
    as_of: datetime


class EventDetailResponse(EventResponse):
    claims: list[ClaimResponse]
    fact_card_id: Optional[str]


class FactCardResponse(BaseModel):
    id: str
    event_id: str
    version: int
    status: str
    report_type: str
    title: str
    summary: str
    claim_ids: list[str]
    as_of: datetime
    disclaimer: str
    supersedes_report_id: Optional[str] = None
    change_reason: Optional[str] = None
    content: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ReportEventGroupResponse(BaseModel):
    """One management-list row representing all report versions for an event."""

    event_id: str
    event_title: str
    latest_report: FactCardResponse
    published_report: Optional[FactCardResponse] = None
    version_count: int
    latest_version: int
    last_updated_at: datetime


class TransmissionStepResponse(BaseModel):
    step: int
    description: str


class TransmissionChainResponse(BaseModel):
    chain_id: str
    mechanism: str
    steps: list[TransmissionStepResponse]
    confidence: float


class ImpactTargetResponse(BaseModel):
    target_type: str
    target_name: str
    target_code: Optional[str] = None
    direction: str
    magnitude: str
    horizon: str
    confidence: float
    rationale: str
    chain_refs: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


class ImpactAnalysisResponse(BaseModel):
    id: str
    event_id: str
    version: int
    status: str
    event_title_snapshot: str
    summary: str
    transmission_chains: list[TransmissionChainResponse]
    impacts: list[ImpactTargetResponse]
    macro_assumptions: list[str]
    watch_items: list[str]
    generated_by: str
    model_run_id: Optional[str] = None
    degraded: bool
    supersedes_id: Optional[str] = None
    created_at: Optional[datetime] = None
    analysis_payload: dict[str, Any] = Field(default_factory=dict)
    quality_report: dict[str, Any] = Field(default_factory=dict)
    edit_revision: int = 0
    derived_from_id: Optional[str] = None
    preliminary_assessment_id: Optional[str] = None


class PreliminaryAssessmentResponse(BaseModel):
    id: str
    event_id: str
    workflow_id: Optional[str] = None
    version: int
    status: str
    event_title_snapshot: str
    as_of: datetime
    summary: str
    thesis: str
    direction: str
    significance: str
    confidence: float
    assessment_payload: dict[str, Any] = Field(default_factory=dict)
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    input_hash: str
    quality_report: dict[str, Any] = Field(default_factory=dict)
    generated_by: str
    model_run_id: Optional[str] = None
    agent_version: str
    prompt_version: str
    supersedes_id: Optional[str] = None
    created_at: Optional[datetime] = None


class ImpactGraphEditRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    graph: dict[str, Any]
    scenarios: list[dict[str, Any]] = Field(default_factory=list)
    impact_assessments: list[dict[str, Any]] = Field(default_factory=list)
    change_reason: str = Field(min_length=1, max_length=2000)


class ImpactGraphLayoutRequest(BaseModel):
    node_positions: dict[str, dict[str, float]] = Field(default_factory=dict)
    collapsed_groups: list[str] = Field(default_factory=list)
    viewport: dict[str, float] = Field(default_factory=dict)


class ImpactAnalysisTransitionRequest(BaseModel):
    status: str = Field(pattern="^(needs_review|approved|rejected|superseded)$")
    comment: Optional[str] = Field(default=None, max_length=2000)


class ImpactTargetResponse(BaseModel):
    id: str
    target_type: str
    target_code: str
    canonical_name: str
    taxonomy_version: str
    parent_target_id: Optional[str] = None
    hierarchy_level: int = 0
    hierarchy_status: str = "approved"
    hierarchy_source: str = "manual"
    propagation_weight: float = 0.85
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class ImpactSnapshotResponse(BaseModel):
    id: str
    target_id: str
    as_of: datetime
    horizon: str
    scenario_set_id: str
    positive_gross: float
    negative_gross: float
    net_score: float
    direction: str
    magnitude: str
    confidence: float
    dominant_event_id: Optional[str] = None
    previous_direction: Optional[str] = None
    change_type: Optional[str] = None
    explanation: str
    contributions: list[dict[str, Any]] = Field(default_factory=list)


class EventImpactRelationRequest(BaseModel):
    source_event_id: str = Field(min_length=1)
    target_event_id: str = Field(min_length=1)
    relation_type: str = Field(
        pattern="^(same_incident|updates|causes|amplifies|offsets|independent)$"
    )
    dependency_weight: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.7, ge=0, le=1)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class ForwardImpactWindowCreateRequest(BaseModel):
    target_id: str = Field(min_length=1)
    as_of: datetime
    window_start: datetime
    window_end: datetime
    event_types: list[str] = Field(default_factory=list)
    catalyst_ids: list[str] = Field(default_factory=list)
    included_kinds: list[str] = Field(default_factory=lambda: ["scheduled", "conditional"])
    granularity: str = Field(default="auto", pattern="^(auto|day|week|month)$")
    scenario_set_id: str = Field(default="baseline", min_length=1)


class ForwardCatalystCreateRequest(BaseModel):
    target_id: str = Field(min_length=1)
    kind: str = Field(pattern="^(scheduled|conditional|hypothetical)$")
    title: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    scheduled_from: Optional[datetime] = None
    scheduled_to: Optional[datetime] = None
    trigger_definition: dict[str, Any] = Field(default_factory=dict)
    probability_low: Optional[float] = Field(default=None, ge=0, le=1)
    probability_base: Optional[float] = Field(default=None, ge=0, le=1)
    probability_high: Optional[float] = Field(default=None, ge=0, le=1)
    probability_basis: str = Field(default="unknown", min_length=1)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class FutureEventCreateRequest(BaseModel):
    event_type: str = Field(min_length=1)
    kind: str = Field(pattern="^(scheduled|conditional|hypothetical)$")
    title: str = Field(min_length=1)
    description: str = ""
    scheduled_from: Optional[datetime] = None
    scheduled_to: Optional[datetime] = None
    source_timezone: str = "Asia/Shanghai"
    time_precision: str = Field(default="unknown", pattern="^(exact|date|window|unknown)$")
    importance: float = Field(default=0.5, ge=0, le=1)
    probability_low: Optional[float] = Field(default=None, ge=0, le=1)
    probability_base: Optional[float] = Field(default=None, ge=0, le=1)
    probability_high: Optional[float] = Field(default=None, ge=0, le=1)
    probability_basis: str = "unknown"
    source_url: Optional[str] = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    target_impacts: list["FutureEventImpactCreate"] = Field(default_factory=list)


class FutureEventTransitionRequest(BaseModel):
    status: str = Field(pattern="^(approved|confirmed|cancelled|rejected|realized)$")
    expected_revision: int = Field(ge=1)
    change_reason: str = Field(default="", max_length=2000)
    realized_event_id: Optional[str] = None


class FutureEventImpactCreate(BaseModel):
    target_id: str = Field(min_length=1)
    scenario_id: str = "baseline"
    direction: str = Field(pattern="^(positive|negative|mixed|uncertain)$")
    magnitude: str = Field(pattern="^(strong|moderate|weak|uncertain)$")
    conditional_strength: float = Field(ge=0, le=1)
    occurrence_probability: Optional[float] = Field(default=None, ge=0, le=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    rationale: str = ""
    onset_at: Optional[datetime] = None
    expected_peak_at: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


FutureEventCreateRequest.model_rebuild()


class BriefEntryResponse(BaseModel):
    report_id: str
    event_id: str
    entity_ids: list[str]
    title: str
    importance: float
    urgency: str
    confidence: float
    novelty: float
    recency: float
    score: float
    rank: int


class BriefResponse(BaseModel):
    id: str
    brief_date: str
    entries: list[BriefEntryResponse]
    candidate_count: int
    rule_version: str


class AdminMetricsResponse(BaseModel):
    """运营与质量总览指标（FR-010 / NFR-007）。"""

    workflows: dict[str, Any]
    models: dict[str, Any]
    sources: dict[str, Any]
    reviews: dict[str, Any]
    outbox: dict[str, Any]
    users: dict[str, Any]
    citations: dict[str, Any]


class RetrievalRetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    retrieval_mode: str = Field(
        default="planned",
        pattern="^(vector|lexical|hybrid|graph|relation|sql|timeseries|planned)$",
    )
    top_k: int = Field(default=10, ge=1, le=100)
    as_of: Optional[datetime] = None
    chunk_types: Optional[list[str]] = None
    source_tiers: Optional[list[str]] = None
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    embedding_model_version: Optional[str] = None


class CitationCandidateResponse(BaseModel):
    document_id: str
    chunk_id: str
    excerpt: str
    locator: dict[str, Any] = Field(default_factory=dict)


class RetrievedItemResponse(BaseModel):
    chunk_id: str
    document_id: str
    source_tier: str
    chunk_type: str
    text: str
    score: float
    backend: str
    backend_scores: dict[str, float] = Field(default_factory=dict)
    citation: CitationCandidateResponse
    embedding_model_version: str = ""
    retrieved_at: Optional[datetime] = None


class RetrievalTraceResponse(BaseModel):
    candidate_count: int
    items: list[RetrievedItemResponse]
    filters: dict[str, Any] = Field(default_factory=dict)
    fusion_method: Optional[str] = None
    backend_coverage: dict[str, int] = Field(default_factory=dict)
    embedding_model_version: str = ""
    generated_at: Optional[datetime] = None
    status: str = "succeeded"
    degradation_reason: Optional[str] = None


class MarketForecastIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_ids: list[str] = Field(min_length=1, max_length=100)
    start: datetime
    end: datetime
    horizon: int = Field(default=1)
    interval: str = Field(default="1d", pattern="^(5m|1d)$")
    as_of: Optional[datetime] = None
    limit: int = Field(default=500, ge=3, le=5000)


class MarketForecastSettlementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forecast_ids: Optional[list[str]] = Field(default=None, max_length=5000)
    evaluation_as_of: Optional[datetime] = None
    flat_band: float = Field(default=0.003, ge=0, le=0.2)


class HistoricalForecastReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_ids: list[str] = Field(min_length=1, max_length=100)
    forecast_from: date
    forecast_to: date
    horizon: int
    lookback_days: int = Field(default=500, ge=30, le=3000)
    publication_lag_minutes: int = Field(default=30, ge=0, le=1440)
    max_slots: int = Field(default=5000, ge=1, le=100000)
    settle_outcomes: bool = True
    evaluation_as_of: Optional[datetime] = None


class MarketCalibrationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_key: str = Field(default="market-outlook", min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=40)
    market: str = Field(pattern="^(cn|hk|us|all)$")
    horizon: int
    instrument_id: Optional[str] = None
    train_start: datetime
    train_end: datetime


class MarketCalibrationTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(pattern="^(published|retired)$")
    reason: str = Field(min_length=3, max_length=500)


class ImpactTargetMappingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1, max_length=128)
    mapping_type: str = Field(pattern="^(instrument|industry|market)$")
    mapping_code: str = Field(min_length=1, max_length=128)
    weight: float = Field(default=1.0, gt=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    reason: str = Field(min_length=3, max_length=500)
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


class ImpactTargetMappingSuggestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1, max_length=128)


class ImpactTargetMappingTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(pattern="^(approved|rejected|retired)$")
    reason: str = Field(min_length=3, max_length=500)


class ImpactProjectionBackfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: Optional[datetime] = None


class IndustryClassificationImportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    level: int = Field(ge=1, le=10)
    parent_code: Optional[str] = Field(default=None, max_length=128)
    aliases: list[str] = Field(default_factory=list, max_length=50)


class InstrumentIndustryMembershipImportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(min_length=1, max_length=128)
    industry_code: str = Field(min_length=1, max_length=128)
    weight: float = Field(default=1.0, gt=0, le=1)
    is_primary: bool = True


class MarketMasterDataImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    standard: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=100)
    effective_from: datetime
    classifications: list[IndustryClassificationImportItem] = Field(min_length=1, max_length=10000)
    memberships: list[InstrumentIndustryMembershipImportItem] = Field(
        default_factory=list, max_length=100000
    )
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class MarketMasterDataImportPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)


class DataEnvelope(BaseModel):
    data: Any
    meta: dict[str, Any]


class ApiErrorBody(BaseModel):
    """DD-00 错误对象。"""

    code: str = Field(description="Stable caller-facing error code")
    message: str
    retryable: bool = False
    details: Any = Field(default_factory=dict)


class ApiErrorMeta(BaseModel):
    request_id: str


class ErrorEnvelope(BaseModel):
    """DD-00 错误响应信封；所有 4xx/5xx 统一使用此形状。"""

    error: ApiErrorBody
    meta: ApiErrorMeta
