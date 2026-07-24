"""Persist versioned model gateway runs for replay."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0005"
down_revision: Optional[str] = "20260712_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    schema = None if op.get_bind().dialect.name == "sqlite" else "platform"
    if "model_runs" in sa.inspect(op.get_bind()).get_table_names(schema=schema):
        return
    op.create_table(
        "model_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("operation", sa.String(80)),
        sa.Column("provider", sa.String(40)),
        sa.Column("model", sa.String(100)),
        sa.Column("input_schema_version", sa.String(40)),
        sa.Column("output_schema_version", sa.String(40)),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("input_payload", sa.JSON()),
        sa.Column("output_payload", sa.JSON()),
        sa.Column("status", sa.String(16)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    op.create_index("ix_model_runs_request_hash", "model_runs", ["request_hash"], schema=schema)


def downgrade() -> None:
    schema = None if op.get_bind().dialect.name == "sqlite" else "platform"
    op.drop_table("model_runs", schema=schema)
