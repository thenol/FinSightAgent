"""Add keyset index for events list (occurred_at, id)."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0016"
down_revision: Optional[str] = "20260723_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    events_schema = None if is_sqlite else "events"

    tables = set(sa.inspect(bind).get_table_names(schema=events_schema))
    if "events" not in tables:
        return

    indexes = {
        idx["name"] for idx in sa.inspect(bind).get_indexes("events", schema=events_schema)
    }
    if "ix_events_occurred_at_id" not in indexes:
        op.create_index(
            "ix_events_occurred_at_id",
            "events",
            ["occurred_at", "id"],
            schema=events_schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    events_schema = None if is_sqlite else "events"

    tables = set(sa.inspect(bind).get_table_names(schema=events_schema))
    if "events" not in tables:
        return

    indexes = {
        idx["name"] for idx in sa.inspect(bind).get_indexes("events", schema=events_schema)
    }
    if "ix_events_occurred_at_id" in indexes:
        op.drop_index(
            "ix_events_occurred_at_id",
            table_name="events",
            schema=events_schema,
        )
