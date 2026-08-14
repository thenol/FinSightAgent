"""agent_runtime

Revision ID: 20260814_0019
Revises: 20260805_0018
Create Date: 2026-08-14 08:00:00.000000
"""
from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0019"
down_revision: Union[str, None] = "20260805_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ANALYSIS_SCHEMA = "analysis"
PLATFORM_SCHEMA = "platform"


def _schema(name: str, is_sqlite: bool) -> Optional[str]:
    return None if is_sqlite else name


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        bind = bind.execution_options(
            schema_translate_map={ANALYSIS_SCHEMA: None, PLATFORM_SCHEMA: None}
        )
    else:
        op.execute(sa.schema.CreateSchema(ANALYSIS_SCHEMA, if_not_exists=True))
        op.execute(sa.schema.CreateSchema(PLATFORM_SCHEMA, if_not_exists=True))

    platform_schema = _schema(PLATFORM_SCHEMA, is_sqlite)
    analysis_schema = _schema(ANALYSIS_SCHEMA, is_sqlite)
    existing_tables = set(sa.inspect(bind).get_table_names(schema=platform_schema))

    if "agent_registrations" not in existing_tables:
        op.create_table(
            "agent_registrations",
            sa.Column("agent_key", sa.String(80), nullable=False),
            sa.Column("version", sa.String(24), nullable=False),
            sa.Column("display_name", sa.String(200), nullable=False),
            sa.Column("capabilities", sa.JSON(), nullable=False, default=list),
            sa.Column("input_schema_refs", sa.JSON(), nullable=False, default=list),
            sa.Column("output_schema_ref", sa.String(120), nullable=False),
            sa.Column("allowed_tools", sa.JSON(), nullable=False, default=list),
            sa.Column("budget_profile", sa.String(40), nullable=False, default="mvp_standard"),
            sa.Column("quality_gates", sa.JSON(), nullable=False, default=dict),
            sa.Column("config", sa.JSON(), nullable=False, default=dict),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("agent_key"),
            sa.UniqueConstraint("agent_key", "version", name="uq_agent_registration_key_version"),
            schema=platform_schema,
        )

    existing_analysis_tables = set(sa.inspect(bind).get_table_names(schema=analysis_schema))

    if "research_plans" not in existing_analysis_tables:
        op.create_table(
            "research_plans",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("workflow_id", sa.String(), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("objective", sa.Text(), nullable=False),
            sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("budget_profile", sa.String(40), nullable=False, default="mvp_standard"),
            sa.Column("completion_criteria", sa.JSON(), nullable=False, default=dict),
            sa.Column("metadata", sa.JSON(), nullable=False, default=dict),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.Index("ix_research_plans_workflow_id", "workflow_id"),
            schema=analysis_schema,
        )

    if "research_tasks" not in existing_analysis_tables:
        op.create_table(
            "research_tasks",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("plan_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("agent_key", sa.String(80), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("dependencies", sa.JSON(), nullable=False, default=list),
            sa.Column("required", sa.Boolean(), nullable=False, default=True),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("input_fields", sa.JSON(), nullable=False, default=list),
            sa.Column("output_field", sa.String(80), nullable=True),
            sa.Column("tool_strategy", sa.JSON(), nullable=False, default=dict),
            sa.Column("output_schema", sa.String(120), nullable=True),
            sa.Column("input_hash", sa.String(64), nullable=True),
            sa.Column("output_snapshot", sa.JSON(), nullable=True, default=None),
            sa.Column("review_reason", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.Index("ix_research_tasks_plan_id", "plan_id"),
            schema=analysis_schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind = bind.execution_options(
            schema_translate_map={ANALYSIS_SCHEMA: None, PLATFORM_SCHEMA: None}
        )

    platform_schema = _schema(PLATFORM_SCHEMA, is_sqlite)
    analysis_schema = _schema(ANALYSIS_SCHEMA, is_sqlite)
    op.drop_table("research_tasks", schema=analysis_schema)
    op.drop_table("research_plans", schema=analysis_schema)
    op.drop_table("agent_registrations", schema=platform_schema)
