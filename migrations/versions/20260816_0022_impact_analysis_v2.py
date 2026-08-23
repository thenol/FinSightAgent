"""impact analysis v2 payload and quality report"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0022"
down_revision: Union[str, None] = "20260815_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    schema = None if is_sqlite else "analysis"
    if not is_sqlite:
        op.execute(sa.schema.CreateSchema("analysis", if_not_exists=True))
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("impact_analyses", schema=schema)}
    if "analysis_payload" not in columns:
        op.add_column("impact_analyses", sa.Column("analysis_payload", sa.JSON(), nullable=True), schema=schema)
    if "quality_report" not in columns:
        op.add_column("impact_analyses", sa.Column("quality_report", sa.JSON(), nullable=True), schema=schema)


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    op.drop_column("impact_analyses", "quality_report", schema=schema)
    op.drop_column("impact_analyses", "analysis_payload", schema=schema)
