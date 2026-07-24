"""Persist workflow state."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0006"
down_revision: Optional[str] = "20260712_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    schema = None if op.get_bind().dialect.name == "sqlite" else "platform"
    if "workflow_runs" in sa.inspect(op.get_bind()).get_table_names(schema=schema):
        return
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("event_id", sa.String()),
        sa.Column("trigger_id", sa.String(80)),
        sa.Column("status", sa.String(24)),
        sa.Column("as_of", sa.DateTime(timezone=True)),
        sa.Column("current_node", sa.String(80)),
        sa.Column("state_version", sa.Integer()),
        sa.Column("blackboard", sa.JSON()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"], schema=schema)


def downgrade() -> None:
    schema = None if op.get_bind().dialect.name == "sqlite" else "platform"
    op.drop_table("workflow_runs", schema=schema)
