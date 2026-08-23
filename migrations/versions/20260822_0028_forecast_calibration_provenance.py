"""link forecast runs to the applied calibration version"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0028"
down_revision: Union[str, None] = "20260822_0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    columns = {
        item["name"]
        for item in sa.inspect(bind).get_columns("market_forecast_runs", schema=schema)
    }
    if "calibration_version_id" not in columns:
        op.add_column(
            "market_forecast_runs",
            sa.Column("calibration_version_id", sa.String(), nullable=True),
            schema=schema,
        )
        op.create_index(
            "ix_market_forecast_runs_calibration_version_id",
            "market_forecast_runs",
            ["calibration_version_id"],
            schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    op.drop_index(
        "ix_market_forecast_runs_calibration_version_id",
        table_name="market_forecast_runs",
        schema=schema,
    )
    op.drop_column("market_forecast_runs", "calibration_version_id", schema=schema)
