"""Persist the structured context used by automatic review decisions."""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0038"
down_revision = "20260827_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "platform"
    columns = {
        item["name"] for item in sa.inspect(bind).get_columns("auto_review_attempts", schema=schema)
    }
    if "context" not in columns:
        op.add_column(
            "auto_review_attempts",
            sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
            schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "platform"
    columns = {
        item["name"] for item in sa.inspect(bind).get_columns("auto_review_attempts", schema=schema)
    }
    if "context" in columns:
        op.drop_column("auto_review_attempts", "context", schema=schema)
