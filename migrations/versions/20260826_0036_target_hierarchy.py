"""Add explicit target hierarchy metadata for cross-level impact views."""

import sqlalchemy as sa
from alembic import op

revision = "20260826_0036"
down_revision = "20260826_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    inspector = sa.inspect(bind)
    if "impact_target_definitions" not in set(inspector.get_table_names(schema=schema)):
        return
    columns = {item["name"] for item in inspector.get_columns("impact_target_definitions", schema=schema)}
    additions = (
        ("parent_target_id", sa.String(), True, None),
        ("hierarchy_level", sa.Integer(), False, "0"),
        ("hierarchy_status", sa.String(24), False, "approved"),
        ("hierarchy_source", sa.String(64), False, "manual"),
        ("propagation_weight", sa.Float(), False, "0.85"),
        ("reviewed_by", sa.String(128), True, None),
        ("reviewed_at", sa.DateTime(timezone=True), True, None),
    )
    for name, column_type, nullable, default in additions:
        if name not in columns:
            kwargs = {"nullable": nullable}
            if default is not None:
                kwargs["server_default"] = default
            op.add_column("impact_target_definitions", sa.Column(name, column_type, **kwargs), schema=schema)
    indexes = {item["name"] for item in inspector.get_indexes("impact_target_definitions", schema=schema)}
    if "ix_impact_target_definitions_parent" not in indexes:
        op.create_index("ix_impact_target_definitions_parent", "impact_target_definitions", ["parent_target_id"], schema=schema)


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    if "impact_target_definitions" not in set(sa.inspect(bind).get_table_names(schema=schema)):
        return
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("impact_target_definitions", schema=schema)}
    if "ix_impact_target_definitions_parent" in indexes:
        op.drop_index("ix_impact_target_definitions_parent", table_name="impact_target_definitions", schema=schema)
    columns = {item["name"] for item in sa.inspect(bind).get_columns("impact_target_definitions", schema=schema)}
    for column in ("hierarchy_level", "parent_target_id", "reviewed_at", "reviewed_by", "propagation_weight", "hierarchy_source", "hierarchy_status"):
        if column in columns:
            op.drop_column("impact_target_definitions", column, schema=schema)
