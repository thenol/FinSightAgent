"""impact graph editor revisions and per-user layout snapshots"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0023"
down_revision: Union[str, None] = "20260816_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    schema = None if is_sqlite else "analysis"
    if not is_sqlite:
        op.execute(sa.schema.CreateSchema("analysis", if_not_exists=True))
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("impact_analyses", schema=schema)}
    if "edit_revision" not in columns:
        op.add_column(
            "impact_analyses",
            sa.Column("edit_revision", sa.Integer(), nullable=False, server_default="0"),
            schema=schema,
        )
    if "derived_from_id" not in columns:
        op.add_column(
            "impact_analyses",
            sa.Column("derived_from_id", sa.String(), nullable=True),
            schema=schema,
        )
    tables = set(inspector.get_table_names(schema=schema))
    if "impact_graph_layouts" not in tables:
        op.create_table(
            "impact_graph_layouts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("analysis_id", sa.String(), nullable=False, index=True),
            sa.Column("user_id", sa.String(), nullable=False, index=True),
            sa.Column("node_positions", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("collapsed_groups", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("viewport", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "analysis_id", "user_id", name="uq_impact_graph_layout_analysis_user"
            ),
            schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    op.drop_table("impact_graph_layouts", schema=schema)
    op.drop_column("impact_analyses", "derived_from_id", schema=schema)
    op.drop_column("impact_analyses", "edit_revision", schema=schema)
