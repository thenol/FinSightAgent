"""persist event time-resolution metadata"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0031"
down_revision: Union[str, None] = "20260823_0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "events"
    columns = {column["name"] for column in sa.inspect(bind).get_columns("events", schema=schema)}
    if "time_resolution" in columns:
        return
    op.add_column(
        "events",
        sa.Column("time_resolution", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        schema=schema,
    )


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "events"
    op.drop_column("events", "time_resolution", schema=schema)
