"""multi-event target impact aggregation"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0024"
down_revision: Union[str, None] = "20260817_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    schema = None if is_sqlite else "analysis"
    if not is_sqlite:
        op.execute(sa.schema.CreateSchema("analysis", if_not_exists=True))

    existing = set(sa.inspect(bind).get_table_names(schema=schema))
    if "impact_target_definitions" not in existing:
        op.create_table(
        "impact_target_definitions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_code", sa.String(128), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("taxonomy_version", sa.String(64), nullable=False, server_default="default-v1"),
        sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("target_type", "target_code", "taxonomy_version", name="uq_impact_target_definition_code"),
        schema=schema,
        )
    if "event_impact_relations" not in existing:
        op.create_table(
        "event_impact_relations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source_event_id", sa.String(), nullable=False),
        sa.Column("target_event_id", sa.String(), nullable=False),
        sa.Column("relation_type", sa.String(24), nullable=False),
        sa.Column("dependency_weight", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(24), nullable=False, server_default="needs_review"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_event_id", "target_event_id", name="uq_event_impact_relation_pair"),
        schema=schema,
        )
    if "impact_contributions" not in existing:
        op.create_table(
        "impact_contributions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("analysis_id", sa.String(), nullable=False),
        sa.Column("assessment_id", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("magnitude", sa.String(16), nullable=False),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("base_strength", sa.Float(), nullable=False),
        sa.Column("effective_strength", sa.Float(), nullable=False),
        sa.Column("event_importance", sa.Float(), nullable=False),
        sa.Column("assessment_confidence", sa.Float(), nullable=False),
        sa.Column("path_confidence", sa.Float(), nullable=False),
        sa.Column("dependency_weight", sa.Float(), nullable=False, server_default="1"),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("expected_peak_at", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("analysis_id", "assessment_id", name="uq_impact_contribution_assessment"),
        schema=schema,
        )
    if "target_impact_snapshots" not in existing:
        op.create_table(
        "target_impact_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("scenario_set_id", sa.String(64), nullable=False),
        sa.Column("positive_gross", sa.Float(), nullable=False),
        sa.Column("negative_gross", sa.Float(), nullable=False),
        sa.Column("net_score", sa.Float(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("magnitude", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("dominant_event_id", sa.String()),
        sa.Column("previous_direction", sa.String(16)),
        sa.Column("change_type", sa.String(32)),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("target_id", "as_of", "horizon", "scenario_set_id", "rule_version", name="uq_target_impact_snapshot_key"),
        schema=schema,
        )
    if "target_impact_snapshot_contributions" not in existing:
        op.create_table(
        "target_impact_snapshot_contributions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("snapshot_id", sa.String(), nullable=False),
        sa.Column("contribution_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("effective_strength", sa.Float(), nullable=False),
        sa.Column("contribution_share", sa.Float(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "contribution_id", name="uq_snapshot_contribution"),
        schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    op.drop_table("target_impact_snapshot_contributions", schema=schema)
    op.drop_table("target_impact_snapshots", schema=schema)
    op.drop_table("impact_contributions", schema=schema)
    op.drop_table("event_impact_relations", schema=schema)
    op.drop_table("impact_target_definitions", schema=schema)
