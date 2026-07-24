"""Create the initial modular-monolith schema with explicit Alembic DDL.

This migration deliberately does not import application metadata.  Models may evolve,
but a historical migration must always describe the schema it originally created.
"""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0001"
down_revision: Optional[str] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMAS = ("ingestion", "events", "evidence", "publishing", "platform")


def _type(kind: str) -> sa.types.TypeEngine:
    return {
        "id": sa.String(),
        "s1": sa.String(1),
        "s8": sa.String(8),
        "s16": sa.String(16),
        "s20": sa.String(20),
        "s24": sa.String(24),
        "s32": sa.String(32),
        "s40": sa.String(40),
        "s50": sa.String(50),
        "s64": sa.String(64),
        "s80": sa.String(80),
        "s100": sa.String(100),
        "s200": sa.String(200),
        "s300": sa.String(300),
        "s500": sa.String(500),
        "text": sa.Text(),
        "json": sa.JSON(),
        "int": sa.Integer(),
        "dt": sa.DateTime(timezone=True),
        "n43": sa.Numeric(4, 3),
        "n54": sa.Numeric(5, 4),
        "n63": sa.Numeric(6, 3),
    }[kind]


def _columns(spec: str) -> list[sa.Column]:
    """Frozen, compact representation of this revision's column DDL."""
    result = []
    for item in spec.split():
        name, kind, *flags = item.split(":")
        result.append(sa.Column(name, _type(kind), primary_key="pk" in flags))
    return result


def _table(schema: str, name: str, spec: str, *constraints: sa.Constraint) -> None:
    op.create_table(name, *_columns(spec), *constraints, schema=schema)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if bind.dialect.name == "postgresql":
        for schema in SCHEMAS:
            existing.update(inspector.get_table_names(schema=schema))
    # Early vertical slices used Base.metadata.create_all().  Treat a complete
    # legacy schema as an adopted baseline so `upgrade head` can stamp it
    # without destructive recreation; later revisions remain normal forwards.
    if {"documents", "events", "claims", "report_versions", "outbox"} <= existing:
        return
    if bind.dialect.name == "postgresql":
        for schema in SCHEMAS:
            op.execute(sa.schema.CreateSchema(schema, if_not_exists=True))
    elif bind.dialect.name == "sqlite":
        # SQLite has no schemas; this keeps the migration testable without changing DDL.
        bind = bind.execution_options(schema_translate_map=dict.fromkeys(SCHEMAS))

    _table(
        "ingestion",
        "sources",
        "id:id:pk code:s64 name:s200 trust_tier:s1 status:s16 feed_url:text allowed_domains:json cursor:text etag:s500 last_modified:s200 last_success_at:dt created_at:dt",
        sa.UniqueConstraint("code", name="uq_sources_code"),
    )
    _table(
        "ingestion",
        "quarantine_items",
        "id:id:pk source_id:id external_id:s300 url:text error_code:s80 detail:text attempts:int status:s16 created_at:dt",
    )
    _table(
        "platform",
        "users",
        "id:id:pk username:s100 password_hash:text role:s32 status:s16 created_at:dt",
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    _table(
        "platform",
        "audit_logs",
        "id:id:pk actor_id:id action:s80 object_type:s80 object_id:id request_id:id details:json created_at:dt",
    )
    _table(
        "ingestion",
        "artifacts",
        "id:id:pk sha256:s64 storage_uri:text mime_type:s100 byte_size:int created_at:dt",
        sa.UniqueConstraint("sha256", name="uq_artifacts_sha256"),
        sa.UniqueConstraint("storage_uri", name="uq_artifacts_storage_uri"),
    )
    _table(
        "ingestion",
        "documents",
        "id:id:pk source_id:id source_tier:s1 external_id:s300 canonical_url:text title:text content:text content_hash:s64 published_at:dt ingested_at:dt version:int",
        sa.UniqueConstraint("source_id", "external_id", name="uq_document_source_external"),
        sa.UniqueConstraint("source_id", "content_hash", name="uq_document_source_content"),
    )
    _table(
        "ingestion",
        "document_revisions",
        "id:id:pk document_id:id revision_no:int artifact_id:id content_hash:s64 normalized_content_uri:text parser_version:s50 metadata:json created_at:dt",
        sa.UniqueConstraint("document_id", "revision_no", name="uq_document_revision_no"),
        sa.UniqueConstraint("document_id", "content_hash", name="uq_document_revision_hash"),
    )
    _table(
        "events",
        "events",
        "id:id:pk event_type:s40 status:s24 title:text entity_ids:json document_ids:json importance:n43 urgency:s16 occurred_at:dt key_fields:json confidence:n43 classifier_version:s50 missing_required:json version:int",
    )
    _table(
        "events",
        "entities",
        "id:id:pk entity_type:s32 canonical_name:s200 status:s16 valid_from:dt valid_to:dt",
    )
    _table(
        "events",
        "securities",
        "id:id:pk entity_id:id ticker:s20 exchange:s16 market_code:s40 valid_from:dt valid_to:dt",
        sa.UniqueConstraint("market_code", "valid_from", name="uq_security_market_code_valid_from"),
    )
    _table(
        "events",
        "entity_aliases",
        "id:id:pk entity_id:id alias:s200 alias_type:s32 language:s8 valid_from:dt valid_to:dt",
        sa.UniqueConstraint("entity_id", "alias", "alias_type", name="uq_entity_alias"),
    )
    _table(
        "events",
        "event_entities",
        "id:id:pk event_id:id entity_id:id market_code:s40 role:s32 confidence:n43 resolution_method:s40",
        sa.UniqueConstraint("event_id", "entity_id", name="uq_event_entity"),
    )
    _table(
        "events",
        "merge_review_tasks",
        "id:id:pk document_id:id candidates:json status:s16 decision:s24 reviewer_id:id decided_at:dt created_at:dt",
    )
    _table(
        "events",
        "match_decisions",
        "id:id:pk document_id:id candidate_event_id:id features:json score:n54 rule_version:s50 decision:s16 created_at:dt",
    )
    _table(
        "evidence",
        "evidence_spans",
        "id:id:pk document_id:id revision_id:id locator_type:s16 locator:json excerpt:text excerpt_hash:s64 extraction_method:s32 extraction_version:s50 created_at:dt",
        sa.UniqueConstraint("revision_id", "excerpt_hash", name="uq_evidence_revision_excerpt"),
    )
    _table(
        "evidence",
        "claims",
        "id:id:pk event_id:id subject_entity_id:id subject_text:text predicate:s80 object_value:json qualifiers:json fingerprint:s64 status:s16 confidence:n43 evidence_ids:json as_of:dt policy_version:s50 version:int",
        sa.UniqueConstraint("event_id", "fingerprint", name="uq_claim_event_fingerprint"),
    )
    _table(
        "evidence",
        "claim_evidence",
        "id:id:pk claim_id:id evidence_id:id stance:s16 source_independence_key:s200 weight:n63",
        sa.UniqueConstraint("claim_id", "evidence_id", name="uq_claim_evidence"),
    )
    _table(
        "evidence",
        "conflicts",
        "id:id:pk event_id:id conflict_type:s24 severity:s16 status:s16 summary:text claim_ids:json resolution:text version:int",
    )
    _table(
        "publishing",
        "report_versions",
        "id:id:pk event_id:id version:int status:s24 report_type:s24 title:text summary:text claim_ids:json as_of:dt disclaimer:text",
        sa.UniqueConstraint("event_id", "version", name="uq_report_event_version"),
    )
    _table(
        "platform",
        "idempotency_keys",
        "key:id:pk request_hash:s64 document_id:id event_id:id fact_card_id:id created_at:dt",
    )
    _table(
        "platform",
        "outbox",
        "id:id:pk event_type:s100 aggregate_id:id payload:json trace_id:id created_at:dt published_at:dt attempts:int next_attempt_at:dt last_error:text dead_lettered_at:dt",
    )
    _table(
        "platform",
        "inbox",
        "id:id:pk consumer:s100 message_id:id received_at:dt processed_at:dt result:json",
        sa.UniqueConstraint("consumer", "message_id", name="uq_inbox_consumer_message"),
    )

    for schema, table, columns, name in (
        ("ingestion", "quarantine_items", ["source_id"], "ix_quarantine_source_id"),
        ("ingestion", "documents", ["source_id"], "ix_documents_source_id"),
        ("ingestion", "document_revisions", ["document_id"], "ix_revisions_document_id"),
        ("events", "events", ["status", "importance"], "ix_event_status_importance"),
        ("events", "securities", ["entity_id"], "ix_securities_entity_id"),
        ("events", "securities", ["market_code"], "ix_securities_market_code"),
        ("events", "entity_aliases", ["entity_id"], "ix_aliases_entity_id"),
        ("events", "event_entities", ["event_id"], "ix_event_entities_event_id"),
        ("events", "event_entities", ["entity_id"], "ix_event_entities_entity_id"),
        ("events", "merge_review_tasks", ["document_id"], "ix_merge_tasks_document_id"),
        ("events", "match_decisions", ["document_id"], "ix_match_decisions_document_id"),
        ("evidence", "evidence_spans", ["document_id"], "ix_evidence_document_id"),
        ("evidence", "evidence_spans", ["revision_id"], "ix_evidence_revision_id"),
        ("evidence", "claims", ["event_id"], "ix_claims_event_id"),
        ("evidence", "claim_evidence", ["claim_id"], "ix_claim_evidence_claim_id"),
        ("evidence", "claim_evidence", ["evidence_id"], "ix_claim_evidence_evidence_id"),
        ("evidence", "conflicts", ["event_id"], "ix_conflicts_event_id"),
        ("publishing", "report_versions", ["event_id"], "ix_reports_event_id"),
        ("platform", "outbox", ["published_at", "created_at"], "ix_outbox_unpublished"),
    ):
        op.create_index(name, table, columns, schema=schema)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind = bind.execution_options(schema_translate_map=dict.fromkeys(SCHEMAS))
    for schema, table in reversed(
        (
            ("ingestion", "sources"),
            ("ingestion", "quarantine_items"),
            ("platform", "users"),
            ("platform", "audit_logs"),
            ("ingestion", "artifacts"),
            ("ingestion", "documents"),
            ("ingestion", "document_revisions"),
            ("events", "events"),
            ("events", "entities"),
            ("events", "securities"),
            ("events", "entity_aliases"),
            ("events", "event_entities"),
            ("events", "merge_review_tasks"),
            ("events", "match_decisions"),
            ("evidence", "evidence_spans"),
            ("evidence", "claims"),
            ("evidence", "claim_evidence"),
            ("evidence", "conflicts"),
            ("publishing", "report_versions"),
            ("platform", "idempotency_keys"),
            ("platform", "outbox"),
            ("platform", "inbox"),
        )
    ):
        op.drop_table(table, schema=schema)
    if bind.dialect.name == "postgresql":
        for schema in reversed(SCHEMAS):
            op.execute(sa.schema.DropSchema(schema, cascade=True, if_exists=True))
