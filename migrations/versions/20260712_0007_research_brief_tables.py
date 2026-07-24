"""Add research runtime and daily brief tables.

Adds the tables introduced for the Agent research loop and daily briefs:
tool_calls, budget_ledger, node_attempts (platform schema) and briefs
(publishing schema). Also adds workflow_runs.budget_profile.
"""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0007"
down_revision: Optional[str] = "20260712_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # --- platform.tool_calls ---
    platform_schema = None if is_sqlite else "platform"
    if "tool_calls" not in sa.inspect(bind).get_table_names(schema=platform_schema):
        op.create_table(
            "tool_calls",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("workflow_id", sa.String()),
            sa.Column("agent_type", sa.String(40)),
            sa.Column("tool_name", sa.String(80)),
            sa.Column("arguments", sa.JSON()),
            sa.Column("result", sa.JSON()),
            sa.Column("as_of", sa.DateTime(timezone=True)),
            sa.Column("status", sa.String(16)),
            sa.Column("error_code", sa.String(80)),
            sa.Column("duration_ms", sa.Integer()),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            schema=platform_schema,
        )
        op.create_index(
            "ix_tool_calls_workflow_id", "tool_calls", ["workflow_id"], schema=platform_schema
        )
        op.create_index(
            "ix_tool_calls_workflow", "tool_calls", ["workflow_id"], schema=platform_schema
        )

    # --- platform.budget_ledger ---
    if "budget_ledger" not in sa.inspect(bind).get_table_names(schema=platform_schema):
        op.create_table(
            "budget_ledger",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("workflow_id", sa.String()),
            sa.Column("node_name", sa.String(80)),
            sa.Column("dimension", sa.Text()),
            sa.Column("entry_type", sa.String(16)),
            sa.Column("amount", sa.Integer()),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            schema=platform_schema,
        )
        op.create_index(
            "ix_budget_ledger_workflow_id", "budget_ledger", ["workflow_id"], schema=platform_schema
        )
        op.create_index(
            "ix_budget_ledger_workflow", "budget_ledger", ["workflow_id"], schema=platform_schema
        )

    # --- platform.node_attempts ---
    if "node_attempts" not in sa.inspect(bind).get_table_names(schema=platform_schema):
        op.create_table(
            "node_attempts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("workflow_id", sa.String()),
            sa.Column("node_name", sa.String(80)),
            sa.Column("attempt_no", sa.Integer()),
            sa.Column("input_hash", sa.String(64)),
            sa.Column("status", sa.String(16)),
            sa.Column("output", sa.JSON()),
            sa.Column("error_code", sa.String(80)),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("ended_at", sa.DateTime(timezone=True)),
            schema=platform_schema,
        )
        op.create_index(
            "ix_node_attempts_workflow_id", "node_attempts", ["workflow_id"], schema=platform_schema
        )
        op.create_index(
            "ix_node_attempts_workflow", "node_attempts", ["workflow_id"], schema=platform_schema
        )

    # --- publishing.briefs ---
    publishing_schema = None if is_sqlite else "publishing"
    if "briefs" not in sa.inspect(bind).get_table_names(schema=publishing_schema):
        op.create_table(
            "briefs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("brief_date", sa.String(10)),
            sa.Column("entries", sa.JSON()),
            sa.Column("candidate_count", sa.Integer()),
            sa.Column("rule_version", sa.String(50)),
            sa.Column("generated_at", sa.DateTime(timezone=True)),
            schema=publishing_schema,
        )
        op.create_index("ix_briefs_brief_date", "briefs", ["brief_date"], schema=publishing_schema)

    # --- workflow_runs.budget_profile ---
    workflow_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("workflow_runs", schema=platform_schema)
    }
    if "budget_profile" not in workflow_columns:
        op.add_column(
            "workflow_runs",
            sa.Column("budget_profile", sa.String(50), server_default="mvp_standard"),
            schema=platform_schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    platform_schema = None if is_sqlite else "platform"
    publishing_schema = None if is_sqlite else "publishing"

    workflow_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("workflow_runs", schema=platform_schema)
    }
    if "budget_profile" in workflow_columns:
        with op.batch_alter_table("workflow_runs", schema=platform_schema) as batch:
            batch.drop_column("budget_profile")

    for table in ("briefs",):
        if table in sa.inspect(bind).get_table_names(schema=publishing_schema):
            op.drop_table(table, schema=publishing_schema)

    for table in ("node_attempts", "budget_ledger", "tool_calls"):
        if table in sa.inspect(bind).get_table_names(schema=platform_schema):
            op.drop_table(table, schema=platform_schema)
