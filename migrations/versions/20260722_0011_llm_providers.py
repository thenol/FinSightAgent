"""Add LLM provider configs and per-agent bindings."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0011"
down_revision: Optional[str] = "20260721_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _schema() -> Optional[str]:
    return None if op.get_bind().dialect.name == "sqlite" else "platform"


def upgrade() -> None:
    bind = op.get_bind()
    schema = _schema()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names(schema=schema))
    if "llm_providers" not in tables:
        op.create_table(
            "llm_providers",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column("display_name", sa.String(length=200), nullable=False),
            sa.Column("protocol", sa.String(length=32), nullable=False),
            sa.Column("base_url", sa.Text(), nullable=False, server_default=""),
            sa.Column("api_key_encrypted", sa.Text(), nullable=False, server_default=""),
            sa.Column("model", sa.String(length=120), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="30"),
            sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="2048"),
            sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"),
            sa.Column("extra_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("code", name="uq_llm_providers_code"),
            schema=schema,
        )
    if "llm_agent_bindings" not in tables:
        op.create_table(
            "llm_agent_bindings",
            sa.Column("agent_key", sa.String(length=64), primary_key=True),
            sa.Column("provider_id", sa.String(), nullable=True),
            sa.Column("model_override", sa.String(length=120), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    schema = _schema()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names(schema=schema))
    if "llm_agent_bindings" in tables:
        op.drop_table("llm_agent_bindings", schema=schema)
    if "llm_providers" in tables:
        op.drop_table("llm_providers", schema=schema)
