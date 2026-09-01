"""Persist source scheduler runtime controls."""

import sqlalchemy as sa
from alembic import op

revision = "20260901_0039"
down_revision = "20260827_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "platform"
    if "source_collection_config" in set(sa.inspect(bind).get_table_names(schema=schema)):
        return
    op.create_table(
        "source_collection_config",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("scheduler_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_crawl_interval_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("retry_limit", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "platform"
    op.drop_table("source_collection_config", schema=schema)
