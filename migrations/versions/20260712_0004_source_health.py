"""Track source health and retry backoff."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0004"
down_revision: Optional[str] = "20260712_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    schema = None if op.get_bind().dialect.name == "sqlite" else "ingestion"
    columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("sources", schema=schema)
    }
    if "consecutive_failures" not in columns:
        op.add_column(
            "sources",
            sa.Column("consecutive_failures", sa.Integer(), server_default="0"),
            schema=schema,
        )
    if "next_retry_at" not in columns:
        op.add_column(
            "sources", sa.Column("next_retry_at", sa.DateTime(timezone=True)), schema=schema
        )
    if "last_error_code" not in columns:
        op.add_column("sources", sa.Column("last_error_code", sa.String(80)), schema=schema)


def downgrade() -> None:
    schema = None if op.get_bind().dialect.name == "sqlite" else "ingestion"
    with op.batch_alter_table("sources", schema=schema) as batch:
        batch.drop_column("last_error_code")
        batch.drop_column("next_retry_at")
        batch.drop_column("consecutive_failures")
