"""persist OOD observations and candidate clusters"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0034"
down_revision: Union[str, None] = "20260824_0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "events"
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names(schema=schema))
    if "ood_observations" not in tables:
        op.create_table(
            "ood_observations",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("event_id", sa.String(), nullable=False),
            sa.Column("document_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="observed"),
            sa.Column("ood_score", sa.Numeric(5, 4), nullable=False),
            sa.Column("financial_relevance", sa.Numeric(5, 4), nullable=False),
            sa.Column("closest_known_types", sa.JSON(), nullable=False),
            sa.Column("extracted_features", sa.JSON(), nullable=False),
            sa.Column("evidence_ids", sa.JSON(), nullable=False),
            sa.Column("classifier_version", sa.String(80), nullable=False, server_default=""),
            sa.Column("router_version", sa.String(40), nullable=False, server_default=""),
            sa.Column("embedding_model_version", sa.String(100)),
            sa.Column("generic_pack_id", sa.String(120)),
            sa.Column("generic_pack_version", sa.String(24)),
            sa.Column("cluster_id", sa.String()),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("as_of", sa.DateTime(timezone=True)),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            schema=schema,
        )
        op.create_index(
            "ix_ood_observations_status", "ood_observations", ["status"], schema=schema
        )
        op.create_index(
            "ix_ood_observations_event_id", "ood_observations", ["event_id"], schema=schema
        )
    if "ood_clusters" not in tables:
        op.create_table(
            "ood_clusters",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("label", sa.String(160), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="forming"),
            sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("independent_source_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cohesion_score", sa.Numeric(5, 4), nullable=False),
            sa.Column("separation_score", sa.Numeric(5, 4), nullable=False),
            sa.Column("stability_score", sa.Numeric(5, 4), nullable=False),
            sa.Column("first_seen_at", sa.DateTime(timezone=True)),
            sa.Column("last_seen_at", sa.DateTime(timezone=True)),
            sa.Column("cluster_version", sa.Integer(), nullable=False, server_default="1"),
            schema=schema,
        )
    if "ood_feature_snapshots" not in tables:
        op.create_table(
            "ood_feature_snapshots",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("observation_id", sa.String(), nullable=False),
            sa.Column("feature_schema_version", sa.String(40), nullable=False),
            sa.Column("features", sa.JSON(), nullable=False),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
            schema=schema,
        )
    if "event_type_proposals" not in tables:
        op.create_table(
            "event_type_proposals",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("cluster_id", sa.String(), nullable=False),
            sa.Column("proposed_label", sa.String(64), nullable=False),
            sa.Column("display_name", sa.String(160), nullable=False),
            sa.Column("definition", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("parent_type", sa.String(64)),
            sa.Column("inclusion_rules", sa.JSON(), nullable=False),
            sa.Column("exclusion_rules", sa.JSON(), nullable=False),
            sa.Column("required_fields", sa.JSON(), nullable=False),
            sa.Column("optional_fields", sa.JSON(), nullable=False),
            sa.Column("mechanisms", sa.JSON(), nullable=False),
            sa.Column("representative_event_ids", sa.JSON(), nullable=False),
            sa.Column("counterexample_event_ids", sa.JSON(), nullable=False),
            sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
            sa.Column("agent_run_id", sa.String()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True)),
            schema=schema,
        )
    if "capability_evaluations" not in tables:
        op.create_table(
            "capability_evaluations",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("pack_id", sa.String(120), nullable=False),
            sa.Column("pack_version", sa.String(24), nullable=False),
            sa.Column("baseline_pack_id", sa.String(120)),
            sa.Column("baseline_pack_version", sa.String(24)),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.Column("comparison", sa.JSON(), nullable=False),
            sa.Column("recommendation", sa.String(80), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            schema=schema,
        )
    if "reprocessing_jobs" not in tables:
        op.create_table(
            "reprocessing_jobs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("source_pack_id", sa.String(120)),
            sa.Column("target_pack_id", sa.String(120), nullable=False),
            sa.Column("event_ids", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "events"
    op.drop_table("ood_clusters", schema=schema)
    op.drop_index("ix_ood_observations_event_id", table_name="ood_observations", schema=schema)
    op.drop_index("ix_ood_observations_status", table_name="ood_observations", schema=schema)
    op.drop_table("ood_observations", schema=schema)
    for table in (
        "reprocessing_jobs",
        "capability_evaluations",
        "event_type_proposals",
        "ood_feature_snapshots",
    ):
        op.drop_table(table, schema=schema)
