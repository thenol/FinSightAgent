"""Add documents.purged_at for hard content destruction after soft-delete."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0015"
down_revision: Optional[str] = "20260723_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = None if is_sqlite else "ingestion"

    tables = set(sa.inspect(bind).get_table_names(schema=ingestion_schema))
    if "documents" not in tables:
        return

    columns = {
        col["name"]
        for col in sa.inspect(bind).get_columns("documents", schema=ingestion_schema)
    }
    if "purged_at" not in columns:
        op.add_column(
            "documents",
            sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
            schema=ingestion_schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = None if is_sqlite else "ingestion"

    columns = {
        col["name"]
        for col in sa.inspect(bind).get_columns("documents", schema=ingestion_schema)
    }
    if "purged_at" in columns:
        op.drop_column("documents", "purged_at", schema=ingestion_schema)
