"""add enterprise roles and dimension-level impact contributions"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0035"
down_revision: Union[str, None] = "20260825_0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("impact_contributions", schema=schema)}
    additions = (
        ("target_role", sa.String(32), "direct_subject"),
        ("relationship_id", sa.String(), None),
        ("relationship_confidence", sa.Float(), "1.0"),
        ("inference_kind", sa.String(24), "derived"),
        ("evidence_refs", sa.JSON(), None),
        ("conditions", sa.JSON(), None),
        ("invalidation_conditions", sa.JSON(), None),
        ("publication_scope", sa.String(20), "official"),
    )
    for name, column_type, default in additions:
        if name not in columns:
            kwargs = {"nullable": True}
            if default is not None:
                kwargs["server_default"] = default
            op.add_column("impact_contributions", sa.Column(name, column_type, **kwargs), schema=schema)
    tables = set(inspector.get_table_names(schema=schema))
    if "impact_dimension_contributions" not in tables:
        op.create_table(
            "impact_dimension_contributions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("contribution_id", sa.String(), nullable=False),
            sa.Column("dimension", sa.String(32), nullable=False),
            sa.Column("direction", sa.String(16), nullable=False),
            sa.Column("magnitude", sa.String(16), nullable=False),
            sa.Column("base_strength", sa.Float(), nullable=False),
            sa.Column("effective_strength", sa.Float(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("quantitative_range", sa.JSON()),
            sa.Column("unit", sa.String(32)),
            sa.Column("evidence_refs", sa.JSON(), nullable=False),
            sa.UniqueConstraint("contribution_id", "dimension", name="uq_impact_dimension_contribution"),
            schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    op.drop_table("impact_dimension_contributions", schema=schema)
    for name in (
        "publication_scope",
        "invalidation_conditions",
        "conditions",
        "evidence_refs",
        "inference_kind",
        "relationship_confidence",
        "relationship_id",
        "target_role",
    ):
        op.drop_column("impact_contributions", name, schema=schema)
