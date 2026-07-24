from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


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
    license: Optional[str] = Field(
        default=None, pattern="^(inherit|full|excerpt|entry_only)$"
    )
    status: Optional[str] = Field(default=None, pattern="^(active|disabled|degraded)$")
    adapter_type: Optional[str] = Field(default=None, pattern="^[a-z0-9_]+$")
    extra_config: Optional[dict[str, Any]] = None


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

    agent_key: str = Field(
        pattern="^(fact_check|company_analysis|skeptic_review|synthesize)$"
    )
    provider_id: Optional[str] = None
    model_override: Optional[str] = Field(default=None, max_length=120)


class LlmAgentBindingBulkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: Optional[str] = None
    model_override: Optional[str] = Field(default=None, max_length=120)


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


class WorkflowCreateRequest(BaseModel):
    trigger_id: str = Field(default="manual", min_length=1, max_length=80)
    as_of: Optional[datetime] = None
    # 默认同步执行图，避免依赖本机 workflow worker；异步入队可传 false
    execute: bool = True


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
