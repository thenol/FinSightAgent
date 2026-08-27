"""Add immutable event preliminary assessment snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0037"
down_revision = "20260826_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    if schema:
        op.execute(sa.schema.CreateSchema("analysis", if_not_exists=True))
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names(schema=schema))
    if "impact_analyses" in tables:
        columns = {item["name"] for item in inspector.get_columns("impact_analyses", schema=schema)}
        if "preliminary_assessment_id" not in columns:
            op.add_column("impact_analyses", sa.Column("preliminary_assessment_id", sa.String(), nullable=True), schema=schema)
            op.create_index("ix_impact_analyses_preliminary_assessment_id", "impact_analyses", ["preliminary_assessment_id"], schema=schema)
    if "event_preliminary_assessments" not in tables:
        op.create_table(
            "event_preliminary_assessments",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("event_id", sa.String(), nullable=False, index=True),
            sa.Column("workflow_id", sa.String(), nullable=True, index=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("event_title_snapshot", sa.Text(), nullable=False),
            sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("thesis", sa.Text(), nullable=False),
            sa.Column("direction", sa.String(16), nullable=False),
            sa.Column("significance", sa.String(16), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("assessment_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("input_snapshot", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("input_hash", sa.String(64), nullable=False),
            sa.Column("quality_report", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("generated_by", sa.String(100), nullable=False),
            sa.Column("model_run_id", sa.String(), nullable=True, index=True),
            sa.Column("agent_version", sa.String(24), nullable=False, server_default="1.0.0"),
            sa.Column("prompt_version", sa.String(80), nullable=False, server_default="preliminary-assessment-v1"),
            sa.Column("supersedes_id", sa.String(), nullable=True, index=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("event_id", "version", name="uq_preliminary_assessment_event_version"),
            schema=schema,
        )
        op.create_index("ix_preliminary_assessments_event_created", "event_preliminary_assessments", ["event_id", "created_at"], schema=schema)
        op.create_index("ix_preliminary_assessments_input_hash", "event_preliminary_assessments", ["event_id", "input_hash"], schema=schema)


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    inspector = sa.inspect(bind)
    if "event_preliminary_assessments" in set(inspector.get_table_names(schema=schema)):
        op.drop_table("event_preliminary_assessments", schema=schema)
    if "impact_analyses" in set(inspector.get_table_names(schema=schema)):
        indexes = {item["name"] for item in inspector.get_indexes("impact_analyses", schema=schema)}
        if "ix_impact_analyses_preliminary_assessment_id" in indexes:
            op.drop_index("ix_impact_analyses_preliminary_assessment_id", table_name="impact_analyses", schema=schema)
        columns = {item["name"] for item in inspector.get_columns("impact_analyses", schema=schema)}
        if "preliminary_assessment_id" in columns:
            op.drop_column("impact_analyses", "preliminary_assessment_id", schema=schema)
