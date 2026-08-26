"""persist review automation policy and attempts"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0032"
down_revision: Union[str, None] = "20260823_0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "platform"
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names(schema=schema))
    if "review_policy" not in tables:
        op.create_table(
            "review_policy",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("mode", sa.String(16), nullable=False, server_default="agent"),
            sa.Column("min_confidence", sa.Float(), nullable=False, server_default="0.85"),
            sa.Column("updated_by", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            schema=schema,
        )
    if "auto_review_attempts" not in tables:
        op.create_table(
            "auto_review_attempts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("object_type", sa.String(40), nullable=False),
            sa.Column("object_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("decision", sa.String(32), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("model_run_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            schema=schema,
        )
    indexes = {item["name"] for item in inspector.get_indexes("auto_review_attempts", schema=schema)} if "auto_review_attempts" in tables else set()
    if "ix_auto_review_attempts_task_created" not in indexes:
        op.create_index("ix_auto_review_attempts_task_created", "auto_review_attempts", ["task_id", "created_at"], schema=schema)
    if "ix_auto_review_attempts_object" not in indexes:
        op.create_index("ix_auto_review_attempts_object", "auto_review_attempts", ["object_id"], schema=schema)


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "platform"
    op.drop_index("ix_auto_review_attempts_object", table_name="auto_review_attempts", schema=schema)
    op.drop_index("ix_auto_review_attempts_task_created", table_name="auto_review_attempts", schema=schema)
    op.drop_table("auto_review_attempts", schema=schema)
    op.drop_table("review_policy", schema=schema)
