"""market forecast runs, outcomes and calibration registry"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0027"
down_revision: Union[str, None] = "20260818_0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    if schema:
        op.execute(sa.schema.CreateSchema("analysis", if_not_exists=True))
    existing = set(sa.inspect(bind).get_table_names(schema=schema))
    if "market_forecast_runs" not in existing:
        op.create_table(
            "market_forecast_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("instrument_id", sa.String(128), nullable=False),
            sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
            sa.Column("horizon", sa.Integer(), nullable=False),
            sa.Column("direction", sa.String(16), nullable=False),
            sa.Column("probabilities", sa.JSON()),
            sa.Column("expected_return_p10", sa.Float()),
            sa.Column("expected_return_p50", sa.Float()),
            sa.Column("expected_return_p90", sa.Float()),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("forecast_status", sa.String(32), nullable=False),
            sa.Column("data_status", sa.String(32), nullable=False),
            sa.Column("rule_version", sa.String(64), nullable=False),
            sa.Column("factor_rule_version", sa.String(64), nullable=False),
            sa.Column("factor_source_hash", sa.String(64), nullable=False),
            sa.Column("source_hash", sa.String(64), nullable=False),
            sa.Column("input_snapshot", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_by", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("source_hash", name="uq_market_forecast_run_source_hash"),
            schema=schema,
        )
        op.create_index(
            "ix_market_forecast_runs_lookup",
            "market_forecast_runs",
            ["instrument_id", "horizon", "as_of"],
            schema=schema,
        )
    if "market_forecast_outcomes" not in existing:
        op.create_table(
            "market_forecast_outcomes",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("forecast_id", sa.String(), nullable=False),
            sa.Column("outcome_observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("realized_return", sa.Float(), nullable=False),
            sa.Column("outcome", sa.String(16), nullable=False),
            sa.Column("base_price", sa.Float(), nullable=False),
            sa.Column("outcome_price", sa.Float(), nullable=False),
            sa.Column("source", sa.String(100), nullable=False),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("label_rule_version", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("forecast_id", name="uq_market_forecast_outcome_forecast"),
            schema=schema,
        )
        op.create_index(
            "ix_market_forecast_outcomes_forecast_id",
            "market_forecast_outcomes",
            ["forecast_id"],
            schema=schema,
        )
        op.create_index(
            "ix_market_forecast_outcomes_observed",
            "market_forecast_outcomes",
            ["outcome_observed_at"],
            schema=schema,
        )
    if "market_calibration_versions" not in existing:
        op.create_table(
            "market_calibration_versions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("model_key", sa.String(80), nullable=False),
            sa.Column("version", sa.String(40), nullable=False),
            sa.Column("horizon", sa.Integer(), nullable=False),
            sa.Column("market", sa.String(16), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("method", sa.String(40), nullable=False),
            sa.Column("parameters", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("train_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("train_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("sample_count", sa.Integer(), nullable=False),
            sa.Column("created_by", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint(
                "model_key", "version", "horizon", "market",
                name="uq_market_calibration_version",
            ),
            schema=schema,
        )
        op.create_index(
            "ix_market_calibration_lookup",
            "market_calibration_versions",
            ["model_key", "market", "horizon", "status"],
            schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    for table in (
        "market_calibration_versions",
        "market_forecast_outcomes",
        "market_forecast_runs",
    ):
        op.drop_table(table, schema=schema)
