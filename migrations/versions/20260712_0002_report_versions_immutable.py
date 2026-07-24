"""Add immutable report-version replacement metadata."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0002"
down_revision: Optional[str] = "20260712_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    schema = None if op.get_bind().dialect.name == "sqlite" else "publishing"
    existing_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("report_versions", schema=schema)
    }
    if {"supersedes_report_id", "change_reason"} <= existing_columns:
        return
    op.add_column(
        "report_versions",
        sa.Column("supersedes_report_id", sa.String(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "report_versions", sa.Column("change_reason", sa.Text(), nullable=True), schema=schema
    )
    op.create_index(
        "ix_report_versions_supersedes", "report_versions", ["supersedes_report_id"], schema=schema
    )


def downgrade() -> None:
    schema = None if op.get_bind().dialect.name == "sqlite" else "publishing"
    with op.batch_alter_table("report_versions", schema=schema) as batch:
        batch.drop_index("ix_report_versions_supersedes")
        batch.drop_column("change_reason")
        batch.drop_column("supersedes_report_id")
