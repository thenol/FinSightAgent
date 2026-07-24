"""Persist AC-008 report content and immutable provenance snapshots."""

from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0010"
down_revision: Optional[str] = "20260721_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _schema() -> str | None:
    return None if op.get_bind().dialect.name == "sqlite" else "publishing"


def upgrade() -> None:
    bind = op.get_bind()
    schema = _schema()
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("report_versions", schema=schema)
    }
    if "content" not in columns:
        op.add_column(
            "report_versions",
            sa.Column("content", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            schema=schema,
        )
    if "provenance" not in columns:
        op.add_column(
            "report_versions",
            sa.Column("provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            schema=schema,
        )

    # Append-only writes are enforced by the repository.  The trigger adds a
    # database boundary for the most consequential case: a published version
    # cannot be edited or deleted behind that repository.
    if bind.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_report_versions_published_no_update
            BEFORE UPDATE ON report_versions
            WHEN OLD.status = 'published'
            BEGIN
                SELECT RAISE(ABORT, 'published report version is immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_report_versions_published_no_delete
            BEFORE DELETE ON report_versions
            WHEN OLD.status = 'published'
            BEGIN
                SELECT RAISE(ABORT, 'published report version is immutable');
            END
            """
        )
    else:
        op.execute(
            """
            CREATE OR REPLACE FUNCTION publishing.reject_published_report_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF OLD.status = 'published' THEN
                    RAISE EXCEPTION 'published report version is immutable';
                END IF;
                RETURN OLD;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_report_versions_published_no_update
            BEFORE UPDATE ON publishing.report_versions
            FOR EACH ROW EXECUTE FUNCTION publishing.reject_published_report_mutation()
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_report_versions_published_no_delete
            BEFORE DELETE ON publishing.report_versions
            FOR EACH ROW EXECUTE FUNCTION publishing.reject_published_report_mutation()
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    schema = _schema()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_report_versions_published_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_report_versions_published_no_update")
    else:
        op.execute("DROP TRIGGER IF EXISTS publishing.trg_report_versions_published_no_delete")
        op.execute("DROP TRIGGER IF EXISTS publishing.trg_report_versions_published_no_update")
        op.execute("DROP FUNCTION IF EXISTS publishing.reject_published_report_mutation()")
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("report_versions", schema=schema)
    }
    with op.batch_alter_table("report_versions", schema=schema) as batch:
        if "provenance" in columns:
            batch.drop_column("provenance")
        if "content" in columns:
            batch.drop_column("content")
