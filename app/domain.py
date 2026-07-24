from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class Document:
    id: str
    source_id: str
    source_tier: str
    external_id: Optional[str]
    canonical_url: Optional[str]
    title: str
    content: str
    content_hash: str
    published_at: datetime
    ingested_at: datetime
    # IMP-052: object lock blocks soft-delete; deleted_at hides from default reads;
    # purged_at means body/evidence destroyed after soft-delete.
    retention_hold: bool = False
    deleted_at: Optional[datetime] = None
    purged_at: Optional[datetime] = None


@dataclass(frozen=True)
class Source:
    id: str
    code: str
    name: str
    trust_tier: str
    feed_url: str
    allowed_domains: list[str]
    status: str = "active"
    adapter_type: str = "rss"
    rate_limit_per_minute: int = 10
    crawl_interval_seconds: int = 3600
    # Content display policy for CitationResolver: inherit|full|excerpt|entry_only
    license: str = "inherit"
    extra_config: dict[str, Any] = field(default_factory=dict)
    cursor: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    last_success_at: Optional[datetime] = None
    consecutive_failures: int = 0
    next_retry_at: Optional[datetime] = None
    last_error_code: Optional[str] = None


@dataclass(frozen=True)
class IngestRun:
    """One source sync attempt (manual, scheduled, or sync-all)."""

    id: str
    source_id: str
    trigger: str
    started_at: datetime
    status: str = "running"
    finished_at: Optional[datetime] = None
    fetched: int = 0
    processed: int = 0
    quarantined: int = 0
    message: Optional[str] = None
    request_id: Optional[str] = None


@dataclass(frozen=True)
class QuarantineItem:
    id: str
    source_id: str
    external_id: Optional[str]
    url: Optional[str]
    error_code: str
    detail: Optional[str]
    attempts: int = 0
    status: str = "open"
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class User:
    id: str
    username: str
    password_hash: str
    role: str
    status: str = "active"
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class AuditLog:
    id: str
    actor_id: Optional[str]
    action: str
    object_type: str
    object_id: Optional[str]
    request_id: Optional[str]
    details: dict[str, Any]
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class ReviewTask:
    id: str
    object_type: str
    object_id: str
    reason_code: str
    allowed_decisions: list[str]
    status: str = "pending"
    decision: Optional[str] = None
    reviewer_id: Optional[str] = None
    comment: Optional[str] = None
    resume_from: Optional[str] = None
    blackboard_version: Optional[int] = None
    created_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None


@dataclass(frozen=True)
class ModelRun:
    id: str
    operation: str
    provider: str
    model: str
    input_schema_version: str
    output_schema_version: str
    request_hash: str
    input_payload: dict[str, Any]
    output_payload: Optional[dict[str, Any]]
    status: str
    latency_ms: int
    estimated_cost_usd: float
    error_code: Optional[str] = None
    created_at: Optional[datetime] = None


LLM_PROTOCOLS = frozenset({"openai_compatible", "anthropic", "deterministic"})
LLM_AGENT_KEYS = frozenset(
    {"fact_check", "company_analysis", "skeptic_review", "synthesize"}
)


@dataclass(frozen=True)
class LlmProviderConfig:
    """Admin-managed LLM endpoint used by ModelGateway (keys stored encrypted)."""

    id: str
    code: str
    display_name: str
    protocol: str
    base_url: str
    api_key_encrypted: str
    model: str
    status: str = "active"
    is_default: bool = False
    timeout_seconds: float = 30.0
    max_tokens: int = 2048
    temperature: float = 0.2
    extra_config: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class LlmAgentBinding:
    """Maps an Agent operation to a provider (None provider_id → default provider)."""

    agent_key: str
    provider_id: Optional[str] = None
    model_override: Optional[str] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class WorkflowRun:
    id: str
    event_id: str
    trigger_id: str
    status: str
    as_of: datetime
    current_node: Optional[str] = None
    state_version: int = 0
    blackboard: dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None
    budget_profile: str = "mvp_standard"
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class BudgetLedgerEntry:
    """预算账本的一笔预留/结算/释放记录。余额通过账本汇总，不就地覆盖。"""

    id: str
    workflow_id: str
    node_name: Optional[str]
    dimension: (
        str  # model_calls|tool_calls|input_tokens|output_tokens|cost_minor_units|elapsed_seconds
    )
    entry_type: str  # reserve|settle|release|adjust
    amount: int
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class NodeAttempt:
    """节点执行的一次尝试，支持幂等复用。"""

    id: str
    workflow_id: str
    node_name: str
    attempt_no: int
    input_hash: str
    status: str  # pending|running|succeeded|failed|skipped
    output: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


@dataclass(frozen=True)
class ToolCall:
    """一次 Agent 工具调用的审计记录。"""

    id: str
    workflow_id: str
    agent_type: str
    tool_name: str
    arguments: dict[str, Any]
    result: Optional[dict[str, Any]]
    as_of: datetime
    status: str  # succeeded | denied | failed
    error_code: Optional[str] = None
    duration_ms: int = 0
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class Artifact:
    id: str
    sha256: str
    storage_uri: str
    mime_type: str
    byte_size: int
    created_at: datetime


@dataclass(frozen=True)
class DocumentRevision:
    id: str
    document_id: str
    revision_no: int
    artifact_id: str
    content_hash: str
    normalized_content_uri: str
    parser_version: str
    created_at: datetime


@dataclass(frozen=True)
class Event:
    id: str
    event_type: str
    status: str
    title: str
    entity_ids: list[str]
    document_ids: list[str]
    importance: float
    urgency: str
    occurred_at: datetime
    version: int = 1
    entity_links: list["EntityLink"] = field(default_factory=list)
    key_fields: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    classifier_version: str = ""
    missing_required: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Entity:
    id: str
    entity_type: str
    canonical_name: str
    status: str
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


@dataclass(frozen=True)
class Security:
    id: str
    entity_id: str
    ticker: str
    exchange: str
    market_code: str
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


@dataclass(frozen=True)
class EntityAlias:
    id: str
    entity_id: str
    alias: str
    alias_type: str
    language: str = "zh"
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


@dataclass(frozen=True)
class EntityLink:
    """事件与实体的对齐关系。"""

    entity_id: str
    market_code: str
    role: str
    confidence: float
    resolution_method: str


@dataclass(frozen=True)
class EntityResolution:
    """单个候选的解析结果。"""

    market_code: str
    entity_id: Optional[str]
    canonical_name: str
    confidence: float
    resolution_method: str
    ambiguous: bool = False


@dataclass(frozen=True)
class MergeReviewTask:
    id: str
    document_id: str
    candidates: list[str]
    status: str
    decision: Optional[str] = None
    reviewer_id: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class MatchFeatures:
    """事件匹配特征分项，用于解释 match_score。"""

    entity_overlap: float
    type_compatibility: float
    key_field_similarity: float
    time_proximity: float
    title_similarity: float
    vetoed: bool = False
    veto_reason: Optional[str] = None

    @property
    def score(self) -> float:
        if self.vetoed:
            return 0.0
        return round(
            0.35 * self.entity_overlap
            + 0.25 * self.type_compatibility
            + 0.20 * self.key_field_similarity
            + 0.10 * self.time_proximity
            + 0.10 * self.title_similarity,
            4,
        )


@dataclass(frozen=True)
class MatchDecision:
    """一次事件匹配决策的审计记录。"""

    id: str
    document_id: str
    candidate_event_id: Optional[str]
    features: dict[str, Any]
    score: float
    rule_version: str
    decision: str  # merged | new | review
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class EvidenceSpan:
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
    deleted_at: Optional[datetime] = None


@dataclass(frozen=True)
class Claim:
    id: str
    event_id: str
    subject_text: str
    predicate: str
    object_value: dict[str, Any]
    status: str
    confidence: float
    evidence_ids: list[str]
    as_of: datetime
    subject_entity_id: Optional[str] = None
    qualifiers: dict[str, Any] = field(default_factory=dict)
    fingerprint: Optional[str] = None
    policy_version: Optional[str] = None


@dataclass(frozen=True)
class ClaimEvidenceRelation:
    claim_id: str
    evidence_id: str
    stance: str
    source_independence_key: str
    weight: float = 1.0


@dataclass(frozen=True)
class ConflictRecord:
    id: str
    event_id: str
    conflict_type: str
    severity: str
    status: str
    summary: str
    claim_ids: list[str]
    resolution: Optional[str] = None
    version: int = 1


@dataclass(frozen=True)
class FactCard:
    id: str
    event_id: str
    version: int
    status: str
    title: str
    summary: str
    claim_ids: list[str]
    as_of: datetime
    report_type: str = "fact_card"
    disclaimer: str = "本内容由自动化系统生成，仅供研究参考，不构成投资建议。"
    supersedes_report_id: Optional[str] = None
    change_reason: Optional[str] = None
    # AC-008 的展示内容采用版本化快照保存；空对象使 0010 前的记录仍可读取。
    content: dict[str, Any] = field(default_factory=dict)
    # 只保存稳定 ID，避免报告版本依赖可变的工作流 Blackboard 或调用结果。
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BriefEntry:
    """简报中的一条候选，含排序分项。"""

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


@dataclass(frozen=True)
class Brief:
    """每日 Top-N 简报：候选集、分数、规则版本与最终顺序，稳定重放。"""

    id: str
    brief_date: str  # YYYY-MM-DD
    entries: list[BriefEntry]
    candidate_count: int
    rule_version: str
    generated_at: Optional[datetime] = None


@dataclass(frozen=True)
class PipelineResult:
    status: str
    document: Document
    event: Event
    evidence: EvidenceSpan
    claim: Claim
    fact_card: FactCard
    warnings: list[str] = field(default_factory=list)
