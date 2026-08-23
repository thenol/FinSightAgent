"""event_type_registry

Revision ID: 20260815_0021
Revises: 20260815_0020
Create Date: 2026-08-15 10:00:00.000000
"""
from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0021"
down_revision: Union[str, None] = "20260815_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVENTS_SCHEMA = "events"


def _schema(name: str, is_sqlite: bool) -> Optional[str]:
    return None if is_sqlite else name


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind = bind.execution_options(
            schema_translate_map={EVENTS_SCHEMA: None}
        )
    else:
        op.execute(sa.schema.CreateSchema(EVENTS_SCHEMA, if_not_exists=True))

    events_schema = _schema(EVENTS_SCHEMA, is_sqlite)
    existing_tables = set(sa.inspect(bind).get_table_names(schema=events_schema))

    if "event_type_registry" not in existing_tables:
        op.create_table(
            "event_type_registry",
            sa.Column("type_label", sa.String(40), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, default="candidate"),
            sa.Column("event_count", sa.Integer(), nullable=False, default=0),
            sa.Column("decided_by", sa.String(), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("type_label"),
            sa.Index("ix_event_type_registry_status", "status"),
            schema=events_schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind = bind.execution_options(
            schema_translate_map={EVENTS_SCHEMA: None}
        )
    op.drop_table("event_type_registry", schema=_schema(EVENTS_SCHEMA, is_sqlite))
