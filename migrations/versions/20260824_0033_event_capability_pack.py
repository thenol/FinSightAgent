"""bind events to the versioned capability pack used for processing"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0033"
down_revision: Union[str, None] = "20260823_0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "events"
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("events", schema=schema)}
    if "capability_pack_id" not in columns:
        op.add_column(
            "events",
            sa.Column("capability_pack_id", sa.String(120), nullable=True),
            schema=schema,
        )
    if "capability_pack_version" not in columns:
        op.add_column(
            "events",
            sa.Column("capability_pack_version", sa.String(24), nullable=True),
            schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "events"
    op.drop_column("events", "capability_pack_version", schema=schema)
    op.drop_column("events", "capability_pack_id", schema=schema)
