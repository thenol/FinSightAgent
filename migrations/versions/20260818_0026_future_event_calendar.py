"""normalized future event calendar with immutable revisions"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0026"
down_revision: Union[str, None] = "20260817_0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    if schema:
        op.execute(sa.schema.CreateSchema("analysis", if_not_exists=True))
    existing = set(sa.inspect(bind).get_table_names(schema=schema))
    if "future_events" not in existing:
        op.create_table(
            "future_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("series_key", sa.String(128)),
            sa.Column("external_id", sa.String(256)),
            sa.Column("source_id", sa.String()),
            sa.Column("current_revision_id", sa.String()),
            sa.Column("realized_event_id", sa.String()),
            sa.Column("created_by", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            schema=schema,
        )
    if "future_event_revisions" not in existing:
        op.create_table(
            "future_event_revisions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("future_event_id", sa.String(), nullable=False),
            sa.Column("revision_no", sa.Integer(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("scheduled_from", sa.DateTime(timezone=True)),
            sa.Column("scheduled_to", sa.DateTime(timezone=True)),
            sa.Column("source_timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
            sa.Column("time_precision", sa.String(16), nullable=False, server_default="unknown"),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("probability_low", sa.Float()),
            sa.Column("probability_base", sa.Float()),
            sa.Column("probability_high", sa.Float()),
            sa.Column("probability_basis", sa.String(64), nullable=False, server_default="unknown"),
            sa.Column("source_url", sa.Text()),
            sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("available_at", sa.DateTime(timezone=True)),
            sa.Column("change_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("supersedes_revision_id", sa.String()),
            sa.Column("created_by", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("future_event_id", "revision_no", name="uq_future_event_revision"),
            schema=schema,
        )
    if "future_event_target_impacts" not in existing:
        op.create_table(
            "future_event_target_impacts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("future_event_id", sa.String(), nullable=False),
            sa.Column("revision_id", sa.String(), nullable=False),
            sa.Column("target_id", sa.String(), nullable=False),
            sa.Column("scenario_id", sa.String(64), nullable=False),
            sa.Column("direction", sa.String(16), nullable=False),
            sa.Column("magnitude", sa.String(16), nullable=False),
            sa.Column("conditional_strength", sa.Float(), nullable=False),
            sa.Column("occurrence_probability", sa.Float()),
            sa.Column("expected_strength", sa.Float()),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
            sa.Column("onset_at", sa.DateTime(timezone=True)),
            sa.Column("expected_peak_at", sa.DateTime(timezone=True)),
            sa.Column("valid_to", sa.DateTime(timezone=True)),
            sa.Column("causal_edge_refs", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "future_event_id", "revision_id", "target_id", "scenario_id",
                name="uq_future_event_target_impact",
            ),
            schema=schema,
        )
    _backfill_legacy(bind, schema)


def _backfill_legacy(bind, schema: str | None) -> None:
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names(schema=schema))
    if "forward_catalysts" not in tables:
        return
    prefix = f"{schema}." if schema else ""
    # Keep the legacy catalyst id as the stable FutureEvent id so existing
    # ForwardImpactWindow.catalyst_ids remain valid during the transition.
    bind.execute(sa.text(f"""
        INSERT INTO {prefix}future_events
            (id, event_type, kind, series_key, external_id, source_id,
             current_revision_id, realized_event_id, created_by, created_at)
        SELECT id, event_type, kind, NULL, NULL, NULL,
               'ftr_' || id, realized_event_id, created_by, created_at
        FROM {prefix}forward_catalysts
        WHERE id NOT IN (SELECT id FROM {prefix}future_events)
    """))
    bind.execute(sa.text(f"""
        INSERT INTO {prefix}future_event_revisions
            (id, future_event_id, revision_no, title, description,
             scheduled_from, scheduled_to, source_timezone, time_precision,
             status, importance, probability_low, probability_base,
             probability_high, probability_basis, source_url, evidence_refs,
             available_at, change_reason, supersedes_revision_id,
             created_by, created_at)
        SELECT 'ftr_' || id, id, 1, title, '', scheduled_from, scheduled_to,
               'Asia/Shanghai', CASE WHEN scheduled_to IS NULL THEN 'date' ELSE 'window' END,
               status, COALESCE((trigger_definition->>'importance')::double precision, 0.5),
               probability_low, probability_base, probability_high,
               probability_basis, NULL, evidence_refs, created_at, 'legacy_forward_catalyst',
               NULL, created_by, created_at
        FROM {prefix}forward_catalysts
        WHERE id NOT IN (SELECT future_event_id FROM {prefix}future_event_revisions)
    """)) if bind.dialect.name == "postgresql" else None
    bind.execute(sa.text(f"""
        INSERT INTO {prefix}future_event_target_impacts
            (id, future_event_id, revision_id, target_id, scenario_id,
             direction, magnitude, conditional_strength, occurrence_probability,
             expected_strength, confidence, rationale, onset_at,
             expected_peak_at, valid_to, causal_edge_refs, evidence_refs,
             status, created_at)
        SELECT 'fti_' || id, id, 'ftr_' || id, target_id, 'baseline',
               COALESCE(trigger_definition->>'direction', 'uncertain'),
               COALESCE(trigger_definition->>'magnitude', 'moderate'),
               COALESCE((trigger_definition->>'strength')::double precision, 0.0),
               probability_base,
               CASE WHEN probability_base IS NULL THEN NULL
                    ELSE COALESCE((trigger_definition->>'strength')::double precision, 0.0) * probability_base END,
               COALESCE((trigger_definition->>'confidence')::double precision, 0.5),
               COALESCE(trigger_definition->>'rationale', ''), scheduled_from,
               scheduled_from, scheduled_to, '[]', evidence_refs, status, created_at
        FROM {prefix}forward_catalysts
        WHERE id NOT IN (SELECT future_event_id FROM {prefix}future_event_target_impacts)
    """)) if bind.dialect.name == "postgresql" else None


def downgrade() -> None:
    bind = op.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "analysis"
    for table in ("future_event_target_impacts", "future_event_revisions", "future_events"):
        op.drop_table(table, schema=schema)
