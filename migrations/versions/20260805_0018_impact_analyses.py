"""impact_analyses

Revision ID: 20260805_0018
Revises: 20260730_0017
Create Date: 2026-08-05 15:00:00.000000
"""
from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0018"
down_revision: Union[str, None] = "20260730_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "analysis"


def _schema(is_sqlite: bool) -> Optional[str]:
    return None if is_sqlite else SCHEMA


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        bind = bind.execution_options(schema_translate_map={SCHEMA: None})
    else:
        op.execute(sa.schema.CreateSchema(SCHEMA, if_not_exists=True))

    schema = _schema(is_sqlite)
    existing_tables = set(sa.inspect(bind).get_table_names(schema=schema))
    if "impact_analyses" in existing_tables:
        return

    op.create_table(
        "impact_analyses",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("event_title_snapshot", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("transmission_chains", sa.JSON(), nullable=False, default=list),
        sa.Column("impacts", sa.JSON(), nullable=False, default=list),
        sa.Column("macro_assumptions", sa.JSON(), nullable=False, default=list),
        sa.Column("watch_items", sa.JSON(), nullable=False, default=list),
        sa.Column("generated_by", sa.String(100), nullable=False),
        sa.Column("model_run_id", sa.String(), nullable=True),
        sa.Column("degraded", sa.Boolean(), nullable=False, default=False),
        sa.Column("supersedes_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "version", name="uq_impact_analysis_event_version"),
        sa.Index("ix_impact_analyses_event_id_created_at", "event_id", "created_at"),
        sa.Index("ix_impact_analyses_model_run_id", "model_run_id"),
        sa.Index("ix_impact_analyses_supersedes_id", "supersedes_id"),
        schema=schema,
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        bind = bind.execution_options(schema_translate_map={SCHEMA: None})

    schema = _schema(is_sqlite)
    op.drop_table("impact_analyses", schema=schema)
