"""embedding_vector_type_pgvector

Revision ID: 7ce31390aa6f
Revises: 87f1b20c2b04
Create Date: 2026-07-30 09:32:55.698153
"""
from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = '7ce31390aa6f'
down_revision: Union[str, None] = '87f1b20c2b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EMBEDDING_DIMENSION = 1536


def _schema(name: str, is_sqlite: bool) -> Optional[str]:
    return None if is_sqlite else name


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        return

    ingestion_schema = _schema("ingestion", is_sqlite)

    # 启用 pgvector 扩展；忽略已存在错误。
    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    existing_tables = set(sa.inspect(bind).get_table_names(schema=ingestion_schema))

    def _alter_json_to_vector(table: str, column: str) -> None:
        if table not in existing_tables:
            return
        columns = {
            col["name"]: col
            for col in sa.inspect(bind).get_columns(table, schema=ingestion_schema)
        }
        if column not in columns:
            return
        current_type = str(columns[column]["type"]).lower()
        if "json" in current_type:
            op.alter_column(
                table,
                column,
                existing_type=sa.JSON(),
                type_=Vector(EMBEDDING_DIMENSION),
                postgresql_using=f'{column}::text::vector({EMBEDDING_DIMENSION})',
                schema=ingestion_schema,
            )

    _alter_json_to_vector("embedding_records", "embedding")
    _alter_json_to_vector("disclosure_groups", "representative_embedding")


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        return

    ingestion_schema = _schema("ingestion", is_sqlite)

    existing_tables = set(sa.inspect(bind).get_table_names(schema=ingestion_schema))

    def _alter_vector_to_json(table: str, column: str) -> None:
        if table not in existing_tables:
            return
        columns = {
            col["name"]: col
            for col in sa.inspect(bind).get_columns(table, schema=ingestion_schema)
        }
        if column not in columns:
            return
        current_type = str(columns[column]["type"]).lower()
        if "vector" in current_type:
            op.alter_column(
                table,
                column,
                existing_type=Vector(EMBEDDING_DIMENSION),
                type_=sa.JSON(),
                postgresql_using=f'{column}::json',
                schema=ingestion_schema,
            )

    _alter_vector_to_json("disclosure_groups", "representative_embedding")
    _alter_vector_to_json("embedding_records", "embedding")
