"""watch_triggers

Revision ID: 20260815_0020
Revises: 20260814_0019
Create Date: 2026-08-15 09:00:00.000000
"""
from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0020"
down_revision: Union[str, None] = "20260814_0019"
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

    if "watch_triggers" not in existing_tables:
        op.create_table(
            "watch_triggers",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("event_id", sa.String(), nullable=False),
            sa.Column("trigger_type", sa.String(32), nullable=False),
            sa.Column("condition", sa.JSON(), nullable=False, default=dict),
            sa.Column("status", sa.String(16), nullable=False, default="armed"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.Index("ix_watch_triggers_event_id", "event_id"),
            sa.Index("ix_watch_triggers_trigger_type", "trigger_type"),
            sa.Index("ix_watch_triggers_status", "status"),
            schema=events_schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind = bind.execution_options(
            schema_translate_map={EVENTS_SCHEMA: None}
        )
    op.drop_table("watch_triggers", schema=_schema(EVENTS_SCHEMA, is_sqlite))
