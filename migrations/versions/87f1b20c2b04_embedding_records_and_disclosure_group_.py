"""embedding_records_and_disclosure_group_vectors

Revision ID: 87f1b20c2b04
Revises: 47de9cd5fbc1
Create Date: 2026-07-30 08:59:44.299664
"""
from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = '87f1b20c2b04'
down_revision: Union[str, None] = '47de9cd5fbc1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _schema(name: str, is_sqlite: bool) -> Optional[str]:
    return None if is_sqlite else name


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = _schema("ingestion", is_sqlite)

    existing_tables = set(sa.inspect(bind).get_table_names(schema=ingestion_schema))

    if "embedding_records" not in existing_tables:
        op.create_table(
            'embedding_records',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('chunk_id', sa.String(), nullable=False),
            sa.Column('embedding_model_version', sa.String(length=50), nullable=False),
            sa.Column('embedding', sa.JSON(), nullable=False),
            sa.Column('content_hash', sa.String(length=64), nullable=False),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('error_code', sa.String(length=80), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('chunk_id', 'embedding_model_version', name='uq_embedding_chunk_model'),
            schema=ingestion_schema,
        )

    if "disclosure_groups" in existing_tables:
        columns = {
            col["name"]
            for col in sa.inspect(bind).get_columns("disclosure_groups", schema=ingestion_schema)
        }
        if "representative_embedding" not in columns:
            op.add_column(
                'disclosure_groups',
                sa.Column('representative_embedding', sa.JSON(), nullable=True),
                schema=ingestion_schema,
            )
        if "embedding_model_version" not in columns:
            op.add_column(
                'disclosure_groups',
                sa.Column('embedding_model_version', sa.String(length=50), nullable=True),
                schema=ingestion_schema,
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = _schema("ingestion", is_sqlite)

    existing_tables = set(sa.inspect(bind).get_table_names(schema=ingestion_schema))

    if "disclosure_groups" in existing_tables:
        columns = {
            col["name"]
            for col in sa.inspect(bind).get_columns("disclosure_groups", schema=ingestion_schema)
        }
        if "embedding_model_version" in columns:
            op.drop_column('disclosure_groups', 'embedding_model_version', schema=ingestion_schema)
        if "representative_embedding" in columns:
            op.drop_column('disclosure_groups', 'representative_embedding', schema=ingestion_schema)

    if "embedding_records" in existing_tables:
        op.drop_table('embedding_records', schema=ingestion_schema)
