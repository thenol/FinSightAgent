"""Add sources.license content display policy."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0013"
down_revision: Optional[str] = "20260722_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = None if is_sqlite else "ingestion"

    tables = set(sa.inspect(bind).get_table_names(schema=ingestion_schema))
    if "sources" not in tables:
        return

    columns = {
        col["name"] for col in sa.inspect(bind).get_columns("sources", schema=ingestion_schema)
    }
    if "license" not in columns:
        op.add_column(
            "sources",
            sa.Column(
                "license",
                sa.String(length=24),
                nullable=False,
                server_default="inherit",
            ),
            schema=ingestion_schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    ingestion_schema = None if is_sqlite else "ingestion"

    columns = {
        col["name"] for col in sa.inspect(bind).get_columns("sources", schema=ingestion_schema)
    }
    if "license" in columns:
        op.drop_column("sources", "license", schema=ingestion_schema)
