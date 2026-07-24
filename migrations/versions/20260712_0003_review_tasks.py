"""Add persistent review queue."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0003"
down_revision: Optional[str] = "20260712_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    schema = None if op.get_bind().dialect.name == "sqlite" else "platform"
    if "review_tasks" in sa.inspect(op.get_bind()).get_table_names(schema=schema):
        return
    op.create_table(
        "review_tasks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("object_type", sa.String(40)),
        sa.Column("object_id", sa.String()),
        sa.Column("reason_code", sa.String(80)),
        sa.Column("allowed_decisions", sa.JSON()),
        sa.Column("status", sa.String(16)),
        sa.Column("decision", sa.String(24)),
        sa.Column("reviewer_id", sa.String()),
        sa.Column("comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    op.create_index(
        "ix_review_tasks_status_created", "review_tasks", ["status", "created_at"], schema=schema
    )
    op.create_index("ix_review_tasks_object_id", "review_tasks", ["object_id"], schema=schema)


def downgrade() -> None:
    schema = None if op.get_bind().dialect.name == "sqlite" else "platform"
    op.drop_table("review_tasks", schema=schema)
