from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 默认 Embedding 维度，与 OpenAI text-embedding-3-small 对齐。
EMBEDDING_DIMENSION = 1536


class Base(DeclarativeBase):
    pass


class SourceModel(Base):
    __tablename__ = "sources"
    __table_args__ = {"schema": "ingestion"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    trust_tier: Mapped[str] = mapped_column(String(1))
    status: Mapped[str] = mapped_column(String(16), default="active")
    feed_url: Mapped[str] = mapped_column(Text, default="")
    allowed_domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    adapter_type: Mapped[str] = mapped_column(String(24), default="rss")
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=10)
    crawl_interval_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    license: Mapped[str] = mapped_column(String(24), default="inherit")
    extra_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cursor: Mapped[Optional[str]] = mapped_column(Text)
    etag: Mapped[Optional[str]] = mapped_column(String(500))
    last_modified: Mapped[Optional[str]] = mapped_column(String(200))
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[Optional[str]] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IngestRunModel(Base):
    __tablename__ = "ingest_runs"
    __table_args__ = (
        Index("ix_ingest_runs_source_started", "source_id", "started_at"),
        {"schema": "platform"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(String, index=True)
    trigger: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    quarantined: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[Optional[str]] = mapped_column(Text)
    request_id: Mapped[Optional[str]] = mapped_column(String(80))


class QuarantineItemModel(Base):
    __tablename__ = "quarantine_items"
    __table_args__ = {"schema": "ingestion"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(String, index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(300))
    url: Mapped[Optional[str]] = mapped_column(Text)
    error_code: Mapped[str] = mapped_column(String(80))
    detail: Mapped[Optional[str]] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "platform"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"schema": "platform"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String)
    action: Mapped[str] = mapped_column(String(80))
    object_type: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[Optional[str]] = mapped_column(String)
    request_id: Mapped[Optional[str]] = mapped_column(String)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReviewTaskModel(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        Index("ix_review_tasks_status_created", "status", "created_at"),
        {"schema": "platform"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String, index=True)
    reason_code: Mapped[str] = mapped_column(String(80))
    allowed_decisions: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    decision: Mapped[Optional[str]] = mapped_column(String(24))
    reviewer_id: Mapped[Optional[str]] = mapped_column(String)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    resume_from: Mapped[Optional[str]] = mapped_column(String(80))
    blackboard_version: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ReviewPolicyModel(Base):
    __tablename__ = "review_policy"
    __table_args__ = {"schema": "platform"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="agent")
    min_confidence: Mapped[float] = mapped_column(Float, default=0.85)
    updated_by: Mapped[Optional[str]] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AutoReviewAttemptModel(Base):
    __tablename__ = "auto_review_attempts"
    __table_args__ = (
        Index("ix_auto_review_attempts_task_created", "task_id", "created_at"),
        {"schema": "platform"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, index=True)
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String(24))
    decision: Mapped[Optional[str]] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    model_run_id: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ModelRunModel(Base):
    __tablename__ = "model_runs"
    __table_args__ = (Index("ix_model_runs_request_hash", "request_hash"), {"schema": "platform"})

    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    input_schema_version: Mapped[str] = mapped_column(String(40))
    output_schema_version: Mapped[str] = mapped_column(String(40))
    request_hash: Mapped[str] = mapped_column(String(64))
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    output_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16))
    latency_ms: Mapped[int] = mapped_column()
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    error_code: Mapped[Optional[str]] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LlmProviderConfigModel(Base):
    __tablename__ = "llm_providers"
    __table_args__ = {"schema": "platform"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    protocol: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(Text, default="")
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="active")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=30.0)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048)
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    extra_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LlmAgentBindingModel(Base):
    __tablename__ = "llm_agent_bindings"
    __table_args__ = {"schema": "platform"}

    agent_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[Optional[str]] = mapped_column(String)
    model_override: Mapped[Optional[str]] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkflowRunModel(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (Index("ix_workflow_runs_status", "status"), {"schema": "platform"})
    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    trigger_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_node: Mapped[Optional[str]] = mapped_column(String(80))
    state_version: Mapped[int] = mapped_column(default=0)
    blackboard: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[Optional[str]] = mapped_column(String(80))
    budget_profile: Mapped[str] = mapped_column(String(50), default="mvp_standard")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ToolCallModel(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_workflow", "workflow_id"), {"schema": "platform"})
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String, index=True)
    agent_type: Mapped[str] = mapped_column(String(40))
    tool_name: Mapped[str] = mapped_column(String(80))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[Optional[str]] = mapped_column(String(80))
    duration_ms: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BudgetLedgerModel(Base):
    __tablename__ = "budget_ledger"
    __table_args__ = (Index("ix_budget_ledger_workflow", "workflow_id"), {"schema": "platform"})
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String, index=True)
    node_name: Mapped[Optional[str]] = mapped_column(String(80))
    dimension: Mapped[str] = mapped_column(Text)
    entry_type: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NodeAttemptModel(Base):
    __tablename__ = "node_attempts"
    __table_args__ = (Index("ix_node_attempts_workflow", "workflow_id"), {"schema": "platform"})
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String, index=True)
    node_name: Mapped[str] = mapped_column(String(80))
    attempt_no: Mapped[int] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    output: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    error_code: Mapped[Optional[str]] = mapped_column(String(80))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ArtifactModel(Base):
    __tablename__ = "artifacts"
    __table_args__ = {"schema": "ingestion"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    storage_uri: Mapped[str] = mapped_column(Text, unique=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    byte_size: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_document_source_external"),
        UniqueConstraint("source_id", "content_hash", name="uq_document_source_content"),
        {"schema": "ingestion"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(String, index=True)
    source_tier: Mapped[str] = mapped_column(String(1))
    external_id: Mapped[Optional[str]] = mapped_column(String(300))
    canonical_url: Mapped[Optional[str]] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(default=1)
    retention_hold: Mapped[bool] = mapped_column(default=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentRevisionModel(Base):
    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision_no", name="uq_document_revision_no"),
        UniqueConstraint("document_id", "content_hash", name="uq_document_revision_hash"),
        {"schema": "ingestion"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, index=True)
    revision_no: Mapped[int] = mapped_column()
    artifact_id: Mapped[Optional[str]] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String(64))
    normalized_content_uri: Mapped[Optional[str]] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(50), default="inline-v1")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ParsedDocumentModel(Base):
    __tablename__ = "parsed_documents"
    __table_args__ = (
        Index("ix_parsed_documents_document_id", "document_id"),
        {"schema": "ingestion"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, index=True)
    revision_id: Mapped[str] = mapped_column(String, index=True)
    parser_version: Mapped[str] = mapped_column(String(50), default="doc-intel-v1")
    parser_run_id: Mapped[str] = mapped_column(String)
    language: Mapped[str] = mapped_column(String(16), default="zh")
    title: Mapped[str] = mapped_column(Text)
    block_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentBlockModel(Base):
    __tablename__ = "document_blocks"
    __table_args__ = (
        Index("ix_document_blocks_revision_id_order", "revision_id", "order_index"),
        {"schema": "ingestion"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    parsed_document_id: Mapped[str] = mapped_column(String, index=True)
    revision_id: Mapped[str] = mapped_column(String, index=True)
    block_type: Mapped[str] = mapped_column(String(24))
    block_id: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column()
    char_end: Mapped[int] = mapped_column()
    order_index: Mapped[int] = mapped_column()
    dom_path: Mapped[Optional[str]] = mapped_column(Text)
    page_no: Mapped[Optional[int]] = mapped_column()
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentChunkModel(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("ix_document_chunks_block_id", "block_id"),
        {"schema": "ingestion"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    block_id: Mapped[str] = mapped_column(String, index=True)
    chunk_type: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column()
    char_end: Mapped[int] = mapped_column()
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding_model_version: Mapped[str] = mapped_column(String(50), default="")
    embedding: Mapped[Optional[list[float]]] = mapped_column(JSON, nullable=True)
    as_of: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmbeddingRecordModel(Base):
    __tablename__ = "embedding_records"
    __table_args__ = (
        UniqueConstraint("chunk_id", "embedding_model_version", name="uq_embedding_chunk_model"),
        {"schema": "ingestion"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String, index=True)
    embedding_model_version: Mapped[str] = mapped_column(String(50))
    # PostgreSQL 使用 pgvector vector 类型；SQLite 测试路径回退到 JSON。
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSION).with_variant(JSON, "sqlite")
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[Optional[str]] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DisclosureGroupModel(Base):
    __tablename__ = "disclosure_groups"
    __table_args__ = (
        Index("ix_disclosure_groups_canonical_content_hash", "canonical_content_hash"),
        {"schema": "ingestion"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    canonical_content_hash: Mapped[str] = mapped_column(String(64))
    canonical_document_id: Mapped[Optional[str]] = mapped_column(String)
    entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    event_type_hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    representative_embedding: Mapped[Optional[list[float]]] = mapped_column(
        Vector(EMBEDDING_DIMENSION).with_variant(JSON, "sqlite"),
        nullable=True,
    )
    embedding_model_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DisclosureGroupMembershipModel(Base):
    __tablename__ = "disclosure_group_memberships"
    __table_args__ = (
        Index("ix_disclosure_group_memberships_document_id", "document_id"),
        {"schema": "ingestion"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    disclosure_group_id: Mapped[str] = mapped_column(String, index=True)
    document_id: Mapped[str] = mapped_column(String, index=True)
    source_tier: Mapped[str] = mapped_column(String(1))
    reason: Mapped[str] = mapped_column(String(32))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventModel(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_event_status_importance", "status", "importance"),
        Index("ix_events_occurred_at_id", "occurred_at", "id"),
        {"schema": "events"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(Text)
    entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    document_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    disclosure_group_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    importance: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    urgency: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    key_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0"))
    classifier_version: Mapped[str] = mapped_column(String(50), default="")
    missing_required: Mapped[list[str]] = mapped_column(JSON, default=list)
    time_resolution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    capability_pack_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    capability_pack_version: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    version: Mapped[int] = mapped_column(default=1)


class EntityModel(Base):
    __tablename__ = "entities"
    __table_args__ = {"schema": "events"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    canonical_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="active")
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class SecurityModel(Base):
    __tablename__ = "securities"
    __table_args__ = (
        UniqueConstraint("market_code", "valid_from", name="uq_security_market_code_valid_from"),
        {"schema": "events"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    entity_id: Mapped[str] = mapped_column(String, index=True)
    ticker: Mapped[str] = mapped_column(String(20))
    exchange: Mapped[str] = mapped_column(String(16))
    market_code: Mapped[str] = mapped_column(String(40), index=True)
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class EntityAliasModel(Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (
        UniqueConstraint("entity_id", "alias", "alias_type", name="uq_entity_alias"),
        {"schema": "events"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    entity_id: Mapped[str] = mapped_column(String, index=True)
    alias: Mapped[str] = mapped_column(String(200))
    alias_type: Mapped[str] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(8), default="zh")
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class EventEntityModel(Base):
    __tablename__ = "event_entities"
    __table_args__ = (
        UniqueConstraint("event_id", "entity_id", name="uq_event_entity"),
        {"schema": "events"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    entity_id: Mapped[str] = mapped_column(String, index=True)
    market_code: Mapped[Optional[str]] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    resolution_method: Mapped[str] = mapped_column(String(40))


class MergeReviewTaskModel(Base):
    __tablename__ = "merge_review_tasks"
    __table_args__ = {"schema": "events"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, index=True)
    candidates: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")
    decision: Mapped[Optional[str]] = mapped_column(String(24))
    reviewer_id: Mapped[Optional[str]] = mapped_column(String)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WatchTriggerModel(Base):
    __tablename__ = "watch_triggers"
    __table_args__ = {"schema": "events"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), index=True)
    condition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="armed", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class EventTypeRegistryModel(Base):
    __tablename__ = "event_type_registry"
    __table_args__ = {"schema": "events"}

    type_label: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    decided_by: Mapped[Optional[str]] = mapped_column(String)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class OODObservationModel(Base):
    __tablename__ = "ood_observations"
    __table_args__ = {"schema": "events"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    document_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String(32), default="observed", index=True)
    ood_score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    financial_relevance: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    closest_known_types: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    extracted_features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    classifier_version: Mapped[str] = mapped_column(String(80), default="")
    router_version: Mapped[str] = mapped_column(String(40), default="")
    embedding_model_version: Mapped[Optional[str]] = mapped_column(String(100))
    generic_pack_id: Mapped[Optional[str]] = mapped_column(String(120))
    generic_pack_version: Mapped[Optional[str]] = mapped_column(String(24))
    cluster_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    as_of: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


class OODClusterModel(Base):
    __tablename__ = "ood_clusters"
    __table_args__ = {"schema": "events"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="forming", index=True)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    independent_source_count: Mapped[int] = mapped_column(Integer, default=0)
    cohesion_score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    separation_score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    stability_score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cluster_version: Mapped[int] = mapped_column(Integer, default=1)


class OODFeatureSnapshotModel(Base):
    __tablename__ = "ood_feature_snapshots"
    __table_args__ = {"schema": "events"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    observation_id: Mapped[str] = mapped_column(String, index=True)
    feature_schema_version: Mapped[str] = mapped_column(String(40))
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventTypeProposalModel(Base):
    __tablename__ = "event_type_proposals"
    __table_args__ = {"schema": "events"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    cluster_id: Mapped[str] = mapped_column(String, index=True)
    proposed_label: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    definition: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    parent_type: Mapped[Optional[str]] = mapped_column(String(64))
    inclusion_rules: Mapped[list[str]] = mapped_column(JSON, default=list)
    exclusion_rules: Mapped[list[str]] = mapped_column(JSON, default=list)
    required_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    optional_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    mechanisms: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    representative_event_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    counterexample_event_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    agent_run_id: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class CapabilityEvaluationModel(Base):
    __tablename__ = "capability_evaluations"
    __table_args__ = {"schema": "events"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(120), index=True)
    pack_version: Mapped[str] = mapped_column(String(24))
    baseline_pack_id: Mapped[Optional[str]] = mapped_column(String(120))
    baseline_pack_version: Mapped[Optional[str]] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    comparison: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recommendation: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReprocessingJobModel(Base):
    __tablename__ = "reprocessing_jobs"
    __table_args__ = {"schema": "events"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_pack_id: Mapped[Optional[str]] = mapped_column(String(120))
    target_pack_id: Mapped[str] = mapped_column(String(120))
    event_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MatchDecisionModel(Base):
    __tablename__ = "match_decisions"
    __table_args__ = {"schema": "events"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, index=True)
    candidate_event_id: Mapped[Optional[str]] = mapped_column(String)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    rule_version: Mapped[str] = mapped_column(String(50))
    decision: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvidenceSpanModel(Base):
    __tablename__ = "evidence_spans"
    __table_args__ = (
        UniqueConstraint("revision_id", "excerpt_hash", name="uq_evidence_revision_excerpt"),
        {"schema": "evidence"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, index=True)
    revision_id: Mapped[str] = mapped_column(String, index=True)
    locator_type: Mapped[str] = mapped_column(String(16))
    locator: Mapped[dict[str, Any]] = mapped_column(JSON)
    excerpt: Mapped[str] = mapped_column(Text)
    excerpt_hash: Mapped[str] = mapped_column(String(64))
    extraction_method: Mapped[str] = mapped_column(String(32))
    extraction_version: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ClaimModel(Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("event_id", "fingerprint", name="uq_claim_event_fingerprint"),
        {"schema": "evidence"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    subject_entity_id: Mapped[Optional[str]] = mapped_column(String)
    subject_text: Mapped[str] = mapped_column(Text)
    predicate: Mapped[str] = mapped_column(String(80))
    object_value: Mapped[dict[str, Any]] = mapped_column(JSON)
    qualifiers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    evidence_ids: Mapped[list[str]] = mapped_column(JSON)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    policy_version: Mapped[str] = mapped_column(String(50), default="policy-v1")
    version: Mapped[int] = mapped_column(default=1)


class ClaimEvidenceRelationModel(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        UniqueConstraint("claim_id", "evidence_id", name="uq_claim_evidence"),
        {"schema": "evidence"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    claim_id: Mapped[str] = mapped_column(String, index=True)
    evidence_id: Mapped[str] = mapped_column(String, index=True)
    stance: Mapped[str] = mapped_column(String(16))
    source_independence_key: Mapped[str] = mapped_column(String(200))
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=Decimal("1.000"))


class ConflictModel(Base):
    __tablename__ = "conflicts"
    __table_args__ = {"schema": "evidence"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    conflict_type: Mapped[str] = mapped_column(String(24))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="open")
    summary: Mapped[str] = mapped_column(Text)
    claim_ids: Mapped[list[str]] = mapped_column(JSON)
    resolution: Mapped[Optional[str]] = mapped_column(Text)
    version: Mapped[int] = mapped_column(default=1)


class FactCardModel(Base):
    __tablename__ = "report_versions"
    __table_args__ = (
        UniqueConstraint("event_id", "version", name="uq_report_event_version"),
        {"schema": "publishing"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(24))
    report_type: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    claim_ids: Mapped[list[str]] = mapped_column(JSON)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    disclaimer: Mapped[str] = mapped_column(Text)
    supersedes_report_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    change_reason: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ImpactAnalysisModel(Base):
    __tablename__ = "impact_analyses"
    __table_args__ = (
        UniqueConstraint("event_id", "version", name="uq_impact_analysis_event_version"),
        Index("ix_impact_analyses_event_id_created_at", "event_id", "created_at"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))
    event_title_snapshot: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    transmission_chains: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    impacts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    macro_assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)
    watch_items: Mapped[list[str]] = mapped_column(JSON, default=list)
    generated_by: Mapped[str] = mapped_column(String(100))
    model_run_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    supersedes_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    analysis_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    edit_revision: Mapped[int] = mapped_column(default=0)
    derived_from_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    preliminary_assessment_id: Mapped[Optional[str]] = mapped_column(String, index=True)


class EventPreliminaryAssessmentModel(Base):
    __tablename__ = "event_preliminary_assessments"
    __table_args__ = (
        UniqueConstraint("event_id", "version", name="uq_preliminary_assessment_event_version"),
        Index("ix_preliminary_assessments_event_created", "event_id", "created_at"),
        Index("ix_preliminary_assessments_input_hash", "event_id", "input_hash"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    workflow_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))
    event_title_snapshot: Mapped[str] = mapped_column(Text)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str] = mapped_column(Text)
    thesis: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(16))
    significance: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column()
    assessment_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_by: Mapped[str] = mapped_column(String(100))
    model_run_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    agent_version: Mapped[str] = mapped_column(String(24), default="1.0.0")
    prompt_version: Mapped[str] = mapped_column(String(80), default="preliminary-assessment-v1")
    supersedes_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ImpactGraphLayoutModel(Base):
    __tablename__ = "impact_graph_layouts"
    __table_args__ = (
        UniqueConstraint("analysis_id", "user_id", name="uq_impact_graph_layout_analysis_user"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    node_positions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    collapsed_groups: Mapped[list[str]] = mapped_column(JSON, default=list)
    viewport: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ImpactTargetDefinitionModel(Base):
    __tablename__ = "impact_target_definitions"
    __table_args__ = (
        UniqueConstraint(
            "target_type",
            "target_code",
            "taxonomy_version",
            name="uq_impact_target_definition_code",
        ),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(32), index=True)
    target_code: Mapped[str] = mapped_column(String(128), index=True)
    canonical_name: Mapped[str] = mapped_column(Text)
    taxonomy_version: Mapped[str] = mapped_column(String(64), default="default-v1")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    parent_target_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    hierarchy_level: Mapped[int] = mapped_column(default=0)
    hierarchy_status: Mapped[str] = mapped_column(String(24), default="approved")
    hierarchy_source: Mapped[str] = mapped_column(String(64), default="manual")
    propagation_weight: Mapped[float] = mapped_column(default=0.85)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(128))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class MarketInstrumentModel(Base):
    __tablename__ = "market_instruments"
    __table_args__ = (
        UniqueConstraint("market", "symbol", "instrument_type", name="uq_market_instrument"),
        {"schema": "platform"},
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    instrument_type: Mapped[str] = mapped_column(String(32), index=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(32))
    currency: Mapped[Optional[str]] = mapped_column(String(16))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    sector_code: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    sector_name: Mapped[Optional[str]] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    provider_symbols: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)


class IndustryTaxonomyModel(Base):
    __tablename__ = "industry_taxonomies"
    __table_args__ = (
        UniqueConstraint("standard", "version", name="uq_industry_taxonomy_version"),
        {"schema": "platform"},
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    standard: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="draft")
    source: Mapped[str] = mapped_column(String(100), default="manual")
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IndustryClassificationModel(Base):
    __tablename__ = "industry_classifications"
    __table_args__ = (
        UniqueConstraint("taxonomy_id", "code", name="uq_industry_classification_code"),
        {"schema": "platform"},
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    taxonomy_id: Mapped[str] = mapped_column(String(128), index=True)
    code: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(200))
    level: Mapped[int] = mapped_column(Integer)
    parent_code: Mapped[Optional[str]] = mapped_column(String(128))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="active")
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class InstrumentIndustryMembershipModel(Base):
    __tablename__ = "instrument_industry_memberships"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "taxonomy_id",
            "industry_code",
            name="uq_instrument_industry_membership",
        ),
        {"schema": "platform"},
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(128), index=True)
    taxonomy_id: Mapped[str] = mapped_column(String(128), index=True)
    industry_code: Mapped[str] = mapped_column(String(128), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default="approved")
    source: Mapped[str] = mapped_column(String(100), default="manual")
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ImpactTargetMappingModel(Base):
    __tablename__ = "impact_target_mappings"
    __table_args__ = (
        UniqueConstraint(
            "target_id",
            "mapping_type",
            "mapping_code",
            name="uq_impact_target_mapping",
        ),
        Index("ix_impact_target_mappings_lookup", "mapping_type", "mapping_code", "status"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(128), index=True)
    mapping_type: Mapped[str] = mapped_column(String(24))
    mapping_code: Mapped[str] = mapped_column(String(128))
    weight: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="proposed")
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(100), default="manual")
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(100))
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MarketMasterDataImportRunModel(Base):
    __tablename__ = "market_master_data_import_runs"
    __table_args__ = (
        UniqueConstraint("source_hash", name="uq_market_master_data_import_source_hash"),
        Index(
            "ix_market_master_data_import_version",
            "standard",
            "version",
            "created_at",
        ),
        {"schema": "platform"},
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    standard: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(100))
    source_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    classification_count: Mapped[int] = mapped_column(Integer)
    membership_count: Mapped[int] = mapped_column(Integer)
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class EventImpactRelationModel(Base):
    __tablename__ = "event_impact_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_event_id", "target_event_id", name="uq_event_impact_relation_pair"
        ),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_event_id: Mapped[str] = mapped_column(String, index=True)
    target_event_id: Mapped[str] = mapped_column(String, index=True)
    relation_type: Mapped[str] = mapped_column(String(24))
    dependency_weight: Mapped[float] = mapped_column()
    confidence: Mapped[float] = mapped_column()
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="needs_review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ImpactContributionModel(Base):
    __tablename__ = "impact_contributions"
    __table_args__ = (
        UniqueConstraint("analysis_id", "assessment_id", name="uq_impact_contribution_assessment"),
        Index("ix_impact_contributions_target", "target_id", "created_at"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    analysis_id: Mapped[str] = mapped_column(String, index=True)
    assessment_id: Mapped[str] = mapped_column(String)
    target_id: Mapped[str] = mapped_column(String, index=True)
    scenario_id: Mapped[str] = mapped_column(String)
    direction: Mapped[str] = mapped_column(String(16))
    magnitude: Mapped[str] = mapped_column(String(16))
    horizon: Mapped[str] = mapped_column(String(16))
    base_strength: Mapped[float] = mapped_column()
    effective_strength: Mapped[float] = mapped_column()
    event_importance: Mapped[float] = mapped_column()
    assessment_confidence: Mapped[float] = mapped_column()
    path_confidence: Mapped[float] = mapped_column()
    dependency_weight: Mapped[float] = mapped_column(default=1.0)
    target_role: Mapped[str] = mapped_column(String(32), default="direct_subject")
    relationship_id: Mapped[Optional[str]] = mapped_column(String)
    relationship_confidence: Mapped[float] = mapped_column(default=1.0)
    inference_kind: Mapped[str] = mapped_column(String(24), default="derived")
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    conditions: Mapped[list[str]] = mapped_column(JSON, default=list)
    invalidation_conditions: Mapped[list[str]] = mapped_column(JSON, default=list)
    publication_scope: Mapped[str] = mapped_column(String(20), default="official")
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expected_peak_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rule_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ImpactDimensionContributionModel(Base):
    __tablename__ = "impact_dimension_contributions"
    __table_args__ = (
        UniqueConstraint("contribution_id", "dimension", name="uq_impact_dimension_contribution"),
        Index("ix_impact_dimension_contributions_dimension", "dimension"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    contribution_id: Mapped[str] = mapped_column(String, index=True)
    dimension: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(16))
    magnitude: Mapped[str] = mapped_column(String(16))
    base_strength: Mapped[float] = mapped_column()
    effective_strength: Mapped[float] = mapped_column()
    confidence: Mapped[float] = mapped_column()
    quantitative_range: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    unit: Mapped[Optional[str]] = mapped_column(String(32))
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class TargetImpactSnapshotModel(Base):
    __tablename__ = "target_impact_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "target_id",
            "as_of",
            "horizon",
            "scenario_set_id",
            "rule_version",
            name="uq_target_impact_snapshot_key",
        ),
        Index("ix_target_impact_snapshots_target_created", "target_id", "created_at"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    target_id: Mapped[str] = mapped_column(String, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    horizon: Mapped[str] = mapped_column(String(16))
    scenario_set_id: Mapped[str] = mapped_column(String(64))
    positive_gross: Mapped[float] = mapped_column()
    negative_gross: Mapped[float] = mapped_column()
    net_score: Mapped[float] = mapped_column()
    direction: Mapped[str] = mapped_column(String(16))
    magnitude: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column()
    dominant_event_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    previous_direction: Mapped[Optional[str]] = mapped_column(String(16))
    change_type: Mapped[Optional[str]] = mapped_column(String(32))
    source_hash: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(64))
    explanation: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TargetImpactSnapshotContributionModel(Base):
    __tablename__ = "target_impact_snapshot_contributions"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "contribution_id", name="uq_snapshot_contribution"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String, index=True)
    contribution_id: Mapped[str] = mapped_column(String, index=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    direction: Mapped[str] = mapped_column(String(16))
    effective_strength: Mapped[float] = mapped_column()
    contribution_share: Mapped[float] = mapped_column()


class MarketForecastRunModel(Base):
    __tablename__ = "market_forecast_runs"
    __table_args__ = (
        UniqueConstraint("source_hash", name="uq_market_forecast_run_source_hash"),
        Index("ix_market_forecast_runs_lookup", "instrument_id", "horizon", "as_of"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(128))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    horizon: Mapped[int] = mapped_column(Integer)
    direction: Mapped[str] = mapped_column(String(16))
    probabilities: Mapped[Optional[dict[str, float]]] = mapped_column(JSON)
    expected_return_p10: Mapped[Optional[float]] = mapped_column(Float)
    expected_return_p50: Mapped[Optional[float]] = mapped_column(Float)
    expected_return_p90: Mapped[Optional[float]] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    forecast_status: Mapped[str] = mapped_column(String(32))
    data_status: Mapped[str] = mapped_column(String(32))
    calibration_version_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    rule_version: Mapped[str] = mapped_column(String(64))
    factor_rule_version: Mapped[str] = mapped_column(String(64))
    factor_source_hash: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MarketForecastOutcomeModel(Base):
    __tablename__ = "market_forecast_outcomes"
    __table_args__ = (
        UniqueConstraint("forecast_id", name="uq_market_forecast_outcome_forecast"),
        Index("ix_market_forecast_outcomes_observed", "outcome_observed_at"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    forecast_id: Mapped[str] = mapped_column(String, index=True)
    outcome_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    realized_return: Mapped[float] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(16))
    base_price: Mapped[float] = mapped_column(Float)
    outcome_price: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(100))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    label_rule_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MarketCalibrationVersionModel(Base):
    __tablename__ = "market_calibration_versions"
    __table_args__ = (
        UniqueConstraint(
            "model_key",
            "version",
            "horizon",
            "market",
            name="uq_market_calibration_version",
        ),
        Index("ix_market_calibration_lookup", "model_key", "market", "horizon", "status"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    model_key: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(40))
    horizon: Mapped[int] = mapped_column(Integer)
    market: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24))
    method: Mapped[str] = mapped_column(String(40))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    train_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    train_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sample_count: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ForwardImpactWindowModel(Base):
    __tablename__ = "forward_impact_windows"
    __table_args__ = ({"schema": "analysis"},)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    target_id: Mapped[str] = mapped_column(String, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    catalyst_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    included_kinds: Mapped[list[str]] = mapped_column(JSON, default=list)
    granularity: Mapped[str] = mapped_column(String(12))
    scenario_set_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    rule_version: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ForwardCatalystModel(Base):
    __tablename__ = "forward_catalysts"
    __table_args__ = ({"schema": "analysis"},)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    target_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(String(64))
    scheduled_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    scheduled_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    trigger_definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    probability_low: Mapped[Optional[float]] = mapped_column()
    probability_base: Mapped[Optional[float]] = mapped_column()
    probability_high: Mapped[Optional[float]] = mapped_column()
    probability_basis: Mapped[str] = mapped_column(String(64))
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24))
    realized_event_id: Mapped[Optional[str]] = mapped_column(String)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FutureEventModel(Base):
    __tablename__ = "future_events"
    __table_args__ = ({"schema": "analysis"},)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    series_key: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(256), index=True)
    source_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    current_revision_id: Mapped[Optional[str]] = mapped_column(String)
    realized_event_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FutureEventRevisionModel(Base):
    __tablename__ = "future_event_revisions"
    __table_args__ = (
        UniqueConstraint("future_event_id", "revision_no", name="uq_future_event_revision"),
        Index("ix_future_event_revision_schedule", "scheduled_from", "scheduled_to"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    future_event_id: Mapped[str] = mapped_column(String, index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    scheduled_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    scheduled_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    source_timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    time_precision: Mapped[str] = mapped_column(String(16), default="unknown")
    status: Mapped[str] = mapped_column(String(24), index=True)
    importance: Mapped[float] = mapped_column(default=0.5)
    probability_low: Mapped[Optional[float]] = mapped_column()
    probability_base: Mapped[Optional[float]] = mapped_column()
    probability_high: Mapped[Optional[float]] = mapped_column()
    probability_basis: Mapped[str] = mapped_column(String(64), default="unknown")
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    available_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    change_reason: Mapped[str] = mapped_column(Text, default="")
    supersedes_revision_id: Mapped[Optional[str]] = mapped_column(String)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FutureEventTargetImpactModel(Base):
    __tablename__ = "future_event_target_impacts"
    __table_args__ = (
        UniqueConstraint(
            "future_event_id",
            "revision_id",
            "target_id",
            "scenario_id",
            name="uq_future_event_target_impact",
        ),
        Index("ix_future_event_target_impact_target_time", "target_id", "onset_at", "valid_to"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    future_event_id: Mapped[str] = mapped_column(String, index=True)
    revision_id: Mapped[str] = mapped_column(String, index=True)
    target_id: Mapped[str] = mapped_column(String, index=True)
    scenario_id: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    magnitude: Mapped[str] = mapped_column(String(16))
    conditional_strength: Mapped[float] = mapped_column()
    occurrence_probability: Mapped[Optional[float]] = mapped_column()
    expected_strength: Mapped[Optional[float]] = mapped_column()
    confidence: Mapped[float] = mapped_column()
    rationale: Mapped[str] = mapped_column(Text, default="")
    onset_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expected_peak_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    causal_edge_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ForwardImpactContributionModel(Base):
    __tablename__ = "forward_impact_contributions"
    __table_args__ = (
        UniqueConstraint(
            "window_id", "catalyst_id", "scenario_id", name="uq_forward_impact_contribution"
        ),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    window_id: Mapped[str] = mapped_column(String, index=True)
    catalyst_id: Mapped[str] = mapped_column(String, index=True)
    target_id: Mapped[str] = mapped_column(String, index=True)
    scenario_id: Mapped[str] = mapped_column(String)
    direction: Mapped[str] = mapped_column(String(16))
    magnitude: Mapped[str] = mapped_column(String(16))
    conditional_strength: Mapped[float] = mapped_column()
    occurrence_probability: Mapped[Optional[float]] = mapped_column()
    expected_strength: Mapped[Optional[float]] = mapped_column()
    confidence: Mapped[float] = mapped_column()
    onset_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expected_peak_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    causal_edge_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ForwardImpactPointModel(Base):
    __tablename__ = "forward_impact_points"
    __table_args__ = ({"schema": "analysis"},)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    window_id: Mapped[str] = mapped_column(String, index=True)
    point_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    scenario_id: Mapped[str] = mapped_column(String)
    positive_conditional: Mapped[float] = mapped_column()
    negative_conditional: Mapped[float] = mapped_column()
    net_conditional: Mapped[float] = mapped_column()
    positive_expected: Mapped[Optional[float]] = mapped_column()
    negative_expected: Mapped[Optional[float]] = mapped_column()
    net_expected: Mapped[Optional[float]] = mapped_column()
    direction: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column()
    dominant_catalyst_id: Mapped[Optional[str]] = mapped_column(String)


class BriefModel(Base):
    __tablename__ = "briefs"
    __table_args__ = ({"schema": "publishing"},)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    brief_date: Mapped[str] = mapped_column(String(10), index=True)
    entries: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    candidate_count: Mapped[int] = mapped_column(default=0)
    rule_version: Mapped[str] = mapped_column(String(50))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IdempotencyModel(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = {"schema": "platform"}

    key: Mapped[str] = mapped_column(String, primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str] = mapped_column(String)
    event_id: Mapped[str] = mapped_column(String)
    fact_card_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxModel(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        Index("ix_outbox_unpublished", "published_at", "created_at"),
        {"schema": "platform"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100))
    aggregate_id: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    trace_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    dead_lettered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class InboxModel(Base):
    __tablename__ = "inbox"
    __table_args__ = (
        UniqueConstraint("consumer", "message_id", name="uq_inbox_consumer_message"),
        {"schema": "platform"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    consumer: Mapped[str] = mapped_column(String(100))
    message_id: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# Agent Runtime models (DD-80)
class AgentRegistrationModel(Base):
    __tablename__ = "agent_registrations"
    __table_args__ = (
        UniqueConstraint("agent_key", "version", name="uq_agent_registration_key_version"),
        {"schema": "platform"},
    )

    agent_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[str] = mapped_column(String(24))
    display_name: Mapped[str] = mapped_column(String(200))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    input_schema_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_schema_ref: Mapped[str] = mapped_column(String(120))
    allowed_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    budget_profile: Mapped[str] = mapped_column(String(40), default="mvp_standard")
    quality_gates: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ResearchPlanModel(Base):
    __tablename__ = "research_plans"
    __table_args__ = (
        Index("ix_research_plans_workflow_id", "workflow_id"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String, index=True)
    question: Mapped[str] = mapped_column(Text)
    objective: Mapped[str] = mapped_column(Text)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24))
    budget_profile: Mapped[str] = mapped_column(String(40), default="mvp_standard")
    completion_criteria: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    plan_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ResearchTaskModel(Base):
    __tablename__ = "research_tasks"
    __table_args__ = (
        Index("ix_research_tasks_plan_id", "plan_id"),
        {"schema": "analysis"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    plan_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String(80))
    agent_key: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    dependencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24))
    input_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_field: Mapped[Optional[str]] = mapped_column(String(80))
    tool_strategy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_schema: Mapped[Optional[str]] = mapped_column(String(120))
    input_hash: Mapped[Optional[str]] = mapped_column(String(64))
    output_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    review_reason: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
