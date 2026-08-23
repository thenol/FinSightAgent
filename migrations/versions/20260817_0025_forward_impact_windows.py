"""forward impact windows and future catalysts"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0025"
down_revision: Union[str, None] = "20260817_0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    if schema:
        op.execute(sa.schema.CreateSchema("analysis", if_not_exists=True))
    existing = set(sa.inspect(bind).get_table_names(schema=schema))
    if "forward_impact_windows" not in existing:
        op.create_table(
            "forward_impact_windows", sa.Column("id", sa.String(), primary_key=True),
            sa.Column("target_id", sa.String(), nullable=False, index=True),
            sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("event_types", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("catalyst_ids", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("included_kinds", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("granularity", sa.String(12), nullable=False),
            sa.Column("scenario_set_id", sa.String(64), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("rule_version", sa.String(64), nullable=False),
            sa.Column("source_hash", sa.String(64), nullable=False),
            sa.Column("created_by", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), schema=schema,
        )
    if "forward_catalysts" not in existing:
        op.create_table(
            "forward_catalysts", sa.Column("id", sa.String(), primary_key=True),
            sa.Column("target_id", sa.String(), nullable=False, index=True),
            sa.Column("kind", sa.String(16), nullable=False), sa.Column("title", sa.Text(), nullable=False),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("scheduled_from", sa.DateTime(timezone=True)), sa.Column("scheduled_to", sa.DateTime(timezone=True)),
            sa.Column("trigger_definition", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("probability_low", sa.Float()), sa.Column("probability_base", sa.Float()), sa.Column("probability_high", sa.Float()),
            sa.Column("probability_basis", sa.String(64), nullable=False), sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(24), nullable=False), sa.Column("realized_event_id", sa.String()),
            sa.Column("created_by", sa.String(100), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), schema=schema,
        )
    if "forward_impact_contributions" not in existing:
        op.create_table(
            "forward_impact_contributions", sa.Column("id", sa.String(), primary_key=True),
            sa.Column("window_id", sa.String(), nullable=False, index=True), sa.Column("catalyst_id", sa.String(), nullable=False, index=True),
            sa.Column("target_id", sa.String(), nullable=False, index=True), sa.Column("scenario_id", sa.String(), nullable=False),
            sa.Column("direction", sa.String(16), nullable=False), sa.Column("magnitude", sa.String(16), nullable=False),
            sa.Column("conditional_strength", sa.Float(), nullable=False), sa.Column("occurrence_probability", sa.Float()), sa.Column("expected_strength", sa.Float()),
            sa.Column("confidence", sa.Float(), nullable=False), sa.Column("onset_at", sa.DateTime(timezone=True)), sa.Column("expected_peak_at", sa.DateTime(timezone=True)), sa.Column("valid_to", sa.DateTime(timezone=True)),
            sa.Column("causal_edge_refs", sa.JSON(), nullable=False, server_default="[]"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("window_id", "catalyst_id", "scenario_id", name="uq_forward_impact_contribution"), schema=schema,
        )
    if "forward_impact_points" not in existing:
        op.create_table(
            "forward_impact_points", sa.Column("id", sa.String(), primary_key=True), sa.Column("window_id", sa.String(), nullable=False, index=True),
            sa.Column("point_at", sa.DateTime(timezone=True), nullable=False, index=True), sa.Column("scenario_id", sa.String(), nullable=False),
            sa.Column("positive_conditional", sa.Float(), nullable=False), sa.Column("negative_conditional", sa.Float(), nullable=False), sa.Column("net_conditional", sa.Float(), nullable=False),
            sa.Column("positive_expected", sa.Float()), sa.Column("negative_expected", sa.Float()), sa.Column("net_expected", sa.Float()),
            sa.Column("direction", sa.String(16), nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("dominant_catalyst_id", sa.String()), schema=schema,
        )


def downgrade() -> None:
    schema = None if op.get_bind().dialect.name == "sqlite" else "analysis"
    for table in ("forward_impact_points", "forward_impact_contributions", "forward_catalysts", "forward_impact_windows"):
        op.drop_table(table, schema=schema)
