"""document_chunks_text_search_gin

Revision ID: 20260730_0017
Revises: 7ce31390aa6f
Create Date: 2026-07-30 14:30:00.000000
"""
from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0017"
down_revision: Union[str, None] = "7ce31390aa6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _schema(name: str, is_sqlite: bool) -> Optional[str]:
    return None if is_sqlite else name


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        return

    ingestion_schema = _schema("ingestion", is_sqlite)
    existing_tables = set(sa.inspect(bind).get_table_names(schema=ingestion_schema))

    if "document_chunks" in existing_tables:
        bind.execute(
            sa.text(
                f"CREATE INDEX IF NOT EXISTS ix_document_chunks_text_search "
                f"ON {ingestion_schema}.document_chunks "
                f"USING GIN (to_tsvector('simple', text))"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        return

    ingestion_schema = _schema("ingestion", is_sqlite)
    bind.execute(
        sa.text(
            f"DROP INDEX IF EXISTS {ingestion_schema}.ix_document_chunks_text_search"
        )
    )
