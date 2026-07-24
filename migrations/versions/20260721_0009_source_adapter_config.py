"""Add adapter_type, rate_limit_per_minute and extra_config to sources."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0009"
down_revision: Optional[str] = "20260721_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    schema = None if is_sqlite else "ingestion"
    columns = {col["name"] for col in sa.inspect(bind).get_columns("sources", schema=schema)}
    if "adapter_type" not in columns:
        op.add_column(
            "sources",
            sa.Column("adapter_type", sa.String(24), nullable=False, server_default="rss"),
            schema=schema,
        )
    if "rate_limit_per_minute" not in columns:
        op.add_column(
            "sources",
            sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="10"),
            schema=schema,
        )
    if "extra_config" not in columns:
        op.add_column(
            "sources",
            sa.Column("extra_config", sa.JSON(), nullable=False, server_default="{}"),
            schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    schema = None if is_sqlite else "ingestion"
    columns = {col["name"] for col in sa.inspect(bind).get_columns("sources", schema=schema)}
    if "extra_config" in columns:
        op.drop_column("sources", "extra_config", schema=schema)
    if "rate_limit_per_minute" in columns:
        op.drop_column("sources", "rate_limit_per_minute", schema=schema)
    if "adapter_type" in columns:
        op.drop_column("sources", "adapter_type", schema=schema)
