"""Add resume_from and blackboard_version to review_tasks."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0008"
down_revision: Optional[str] = "20260712_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    schema = None if is_sqlite else "platform"
    columns = {col["name"] for col in sa.inspect(bind).get_columns("review_tasks", schema=schema)}
    if "resume_from" not in columns:
        op.add_column(
            "review_tasks",
            sa.Column("resume_from", sa.String(80), nullable=True),
            schema=schema,
        )
    if "blackboard_version" not in columns:
        op.add_column(
            "review_tasks",
            sa.Column("blackboard_version", sa.Integer(), nullable=True),
            schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    schema = None if is_sqlite else "platform"
    columns = {col["name"] for col in sa.inspect(bind).get_columns("review_tasks", schema=schema)}
    if "blackboard_version" in columns:
        op.drop_column("review_tasks", "blackboard_version", schema=schema)
    if "resume_from" in columns:
        op.drop_column("review_tasks", "resume_from", schema=schema)
