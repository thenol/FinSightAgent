from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
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
