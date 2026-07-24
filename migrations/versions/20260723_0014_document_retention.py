"""Add document retention_hold/deleted_at and evidence soft-delete."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0014"
down_revision: Optional[str] = "20260723_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = None if is_sqlite else "ingestion"
    evidence_schema = None if is_sqlite else "evidence"

    tables = set(sa.inspect(bind).get_table_names(schema=ingestion_schema))
    if "documents" in tables:
        columns = {
            col["name"]
            for col in sa.inspect(bind).get_columns("documents", schema=ingestion_schema)
        }
        if "retention_hold" not in columns:
            op.add_column(
                "documents",
                sa.Column(
                    "retention_hold",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
                schema=ingestion_schema,
            )
        if "deleted_at" not in columns:
            op.add_column(
                "documents",
                sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
                schema=ingestion_schema,
            )

    evidence_tables = set(sa.inspect(bind).get_table_names(schema=evidence_schema))
    if "evidence_spans" in evidence_tables:
        columns = {
            col["name"]
            for col in sa.inspect(bind).get_columns(
                "evidence_spans", schema=evidence_schema
            )
        }
        if "deleted_at" not in columns:
            op.add_column(
                "evidence_spans",
                sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
                schema=evidence_schema,
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = None if is_sqlite else "ingestion"
    evidence_schema = None if is_sqlite else "evidence"

    doc_columns = {
        col["name"]
        for col in sa.inspect(bind).get_columns("documents", schema=ingestion_schema)
    }
    if "deleted_at" in doc_columns:
        op.drop_column("documents", "deleted_at", schema=ingestion_schema)
    if "retention_hold" in doc_columns:
        op.drop_column("documents", "retention_hold", schema=ingestion_schema)

    evidence_columns = {
        col["name"]
        for col in sa.inspect(bind).get_columns(
            "evidence_spans", schema=evidence_schema
        )
    }
    if "deleted_at" in evidence_columns:
        op.drop_column("evidence_spans", "deleted_at", schema=evidence_schema)
