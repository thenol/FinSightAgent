"""document intelligence parsed blocks chunks disclosure groups

Revision ID: 47de9cd5fbc1
Revises: 20260723_0016
Create Date: 2026-07-29 22:42:56.145313
"""
from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = '47de9cd5fbc1'
down_revision: Union[str, None] = '20260723_0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _schema(name: str, is_sqlite: bool) -> Optional[str]:
    return None if is_sqlite else name


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = _schema("ingestion", is_sqlite)
    events_schema = _schema("events", is_sqlite)

    existing_tables = set(sa.inspect(bind).get_table_names(schema=ingestion_schema))

    if "parsed_documents" not in existing_tables:
        op.create_table(
            'parsed_documents',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('document_id', sa.String(), nullable=False),
            sa.Column('revision_id', sa.String(), nullable=False),
            sa.Column('parser_version', sa.String(length=50), nullable=False),
            sa.Column('parser_run_id', sa.String(), nullable=False),
            sa.Column('language', sa.String(length=16), nullable=False),
            sa.Column('title', sa.Text(), nullable=False),
            sa.Column('block_ids', sa.JSON(), nullable=False),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.Index('ix_parsed_documents_document_id', 'document_id'),
            schema=ingestion_schema,
        )

    if "document_blocks" not in existing_tables:
        op.create_table(
            'document_blocks',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('parsed_document_id', sa.String(), nullable=False),
            sa.Column('revision_id', sa.String(), nullable=False),
            sa.Column('block_type', sa.String(length=24), nullable=False),
            sa.Column('block_id', sa.String(length=80), nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            sa.Column('char_start', sa.Integer(), nullable=False),
            sa.Column('char_end', sa.Integer(), nullable=False),
            sa.Column('order_index', sa.Integer(), nullable=False),
            sa.Column('dom_path', sa.Text(), nullable=True),
            sa.Column('page_no', sa.Integer(), nullable=True),
            sa.Column('metadata', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.Index('ix_document_blocks_revision_id_order', 'revision_id', 'order_index'),
            schema=ingestion_schema,
        )

    if "document_chunks" not in existing_tables:
        op.create_table(
            'document_chunks',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('block_id', sa.String(), nullable=False),
            sa.Column('chunk_type', sa.String(length=32), nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            sa.Column('char_start', sa.Integer(), nullable=False),
            sa.Column('char_end', sa.Integer(), nullable=False),
            sa.Column('content_hash', sa.String(length=64), nullable=False),
            sa.Column('embedding_model_version', sa.String(length=50), nullable=False),
            sa.Column('embedding', sa.JSON(), nullable=True),
            sa.Column('as_of', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.Index('ix_document_chunks_block_id', 'block_id'),
            schema=ingestion_schema,
        )

    if "disclosure_groups" not in existing_tables:
        op.create_table(
            'disclosure_groups',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('canonical_content_hash', sa.String(length=64), nullable=False),
            sa.Column('canonical_document_id', sa.String(), nullable=True),
            sa.Column('entity_ids', sa.JSON(), nullable=False),
            sa.Column('event_type_hints', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.Index('ix_disclosure_groups_canonical_content_hash', 'canonical_content_hash'),
            schema=ingestion_schema,
        )

    if "disclosure_group_memberships" not in existing_tables:
        op.create_table(
            'disclosure_group_memberships',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('disclosure_group_id', sa.String(), nullable=False),
            sa.Column('document_id', sa.String(), nullable=False),
            sa.Column('source_tier', sa.String(length=1), nullable=False),
            sa.Column('reason', sa.String(length=32), nullable=False),
            sa.Column('joined_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.Index('ix_disclosure_group_memberships_document_id', 'document_id'),
            sa.Index('ix_disclosure_group_memberships_group_id', 'disclosure_group_id'),
            schema=ingestion_schema,
        )

    events_tables = set(sa.inspect(bind).get_table_names(schema=events_schema))
    if "events" in events_tables:
        events_columns = {
            col["name"]
            for col in sa.inspect(bind).get_columns("events", schema=events_schema)
        }
        if "disclosure_group_id" not in events_columns:
            op.add_column(
                'events',
                sa.Column('disclosure_group_id', sa.String(), nullable=True),
                schema=events_schema,
            )
            op.create_index(
                'ix_events_disclosure_group_id',
                'events',
                ['disclosure_group_id'],
                schema=events_schema,
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = _schema("ingestion", is_sqlite)
    events_schema = _schema("events", is_sqlite)

    events_tables = set(sa.inspect(bind).get_table_names(schema=events_schema))
    if "events" in events_tables:
        events_columns = {
            col["name"]
            for col in sa.inspect(bind).get_columns("events", schema=events_schema)
        }
        if "disclosure_group_id" in events_columns:
            op.drop_index('ix_events_disclosure_group_id', table_name='events', schema=events_schema)
            op.drop_column('events', 'disclosure_group_id', schema=events_schema)

    existing_tables = set(sa.inspect(bind).get_table_names(schema=ingestion_schema))
    for table in (
        'disclosure_group_memberships',
        'disclosure_groups',
        'document_chunks',
        'document_blocks',
        'parsed_documents',
    ):
        if table in existing_tables:
            op.drop_table(table, schema=ingestion_schema)
