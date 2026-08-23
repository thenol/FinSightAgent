"""persist governed market master-data import runs"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0030"
down_revision: Union[str, None] = "20260822_0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "platform"
    if "market_master_data_import_runs" in sa.inspect(bind).get_table_names(schema=schema):
        return
    op.create_table(
        "market_master_data_import_runs",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("standard", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("classification_count", sa.Integer(), nullable=False),
        sa.Column("membership_count", sa.Integer(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source_hash", name="uq_market_master_data_import_source_hash"),
        schema=schema,
    )
    op.create_index(
        "ix_market_master_data_import_version",
        "market_master_data_import_runs",
        ["standard", "version", "created_at"],
        schema=schema,
    )


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "platform"
    op.drop_table("market_master_data_import_runs", schema=schema)
