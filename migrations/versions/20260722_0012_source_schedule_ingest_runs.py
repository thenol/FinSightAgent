"""Add crawl_interval_seconds to sources and platform.ingest_runs."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0012"
down_revision: Optional[str] = "20260722_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = None if is_sqlite else "ingestion"
    platform_schema = None if is_sqlite else "platform"

    columns = {
        col["name"] for col in sa.inspect(bind).get_columns("sources", schema=ingestion_schema)
    }
    if "crawl_interval_seconds" not in columns:
        op.add_column(
            "sources",
            sa.Column(
                "crawl_interval_seconds",
                sa.Integer(),
                nullable=False,
                server_default="3600",
            ),
            schema=ingestion_schema,
        )

    tables = set(sa.inspect(bind).get_table_names(schema=platform_schema))
    if "ingest_runs" not in tables:
        op.create_table(
            "ingest_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("trigger", sa.String(24), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="running"),
            sa.Column("fetched", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("quarantined", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("request_id", sa.String(80), nullable=True),
            schema=platform_schema,
        )
        op.create_index(
            "ix_ingest_runs_source_started",
            "ingest_runs",
            ["source_id", "started_at"],
            schema=platform_schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = None if is_sqlite else "ingestion"
    platform_schema = None if is_sqlite else "platform"

    tables = set(sa.inspect(bind).get_table_names(schema=platform_schema))
    if "ingest_runs" in tables:
        op.drop_index(
            "ix_ingest_runs_source_started",
            table_name="ingest_runs",
            schema=platform_schema,
        )
        op.drop_table("ingest_runs", schema=platform_schema)

    columns = {
        col["name"] for col in sa.inspect(bind).get_columns("sources", schema=ingestion_schema)
    }
    if "crawl_interval_seconds" in columns:
        op.drop_column("sources", "crawl_interval_seconds", schema=ingestion_schema)
