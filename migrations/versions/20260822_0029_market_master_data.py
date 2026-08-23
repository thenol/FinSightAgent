"""add governed market instruments, industry taxonomy, and target mappings"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0029"
down_revision: Union[str, None] = "20260822_0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _schema(bind, name: str) -> str | None:
    return None if bind.dialect.name == "sqlite" else name


def upgrade() -> None:
    bind = op.get_bind()
    platform = _schema(bind, "platform")
    analysis = _schema(bind, "analysis")
    inspector = sa.inspect(bind)
    expected = {
        "market_instruments",
        "industry_taxonomies",
        "industry_classifications",
        "instrument_industry_memberships",
    }
    platform_tables = set(inspector.get_table_names(schema=platform))
    analysis_tables = set(inspector.get_table_names(schema=analysis))
    # ``Base.metadata.create_all`` legacy installations already contain the
    # complete target schema and are adopted by the migration chain.
    if expected <= platform_tables and "impact_target_mappings" in analysis_tables:
        return
    op.create_table(
        "market_instruments",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("instrument_type", sa.String(32), nullable=False),
        sa.Column("exchange", sa.String(32)),
        sa.Column("currency", sa.String(16)),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("sector_code", sa.String(128)),
        sa.Column("sector_name", sa.String(200)),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        sa.Column("provider_symbols", sa.JSON(), nullable=False),
        sa.UniqueConstraint("market", "symbol", "instrument_type", name="uq_market_instrument"),
        schema=platform,
    )
    for name, columns in (
        ("ix_market_instruments_market", ["market"]),
        ("ix_market_instruments_symbol", ["symbol"]),
        ("ix_market_instruments_instrument_type", ["instrument_type"]),
        ("ix_market_instruments_sector_code", ["sector_code"]),
    ):
        op.create_index(name, "market_instruments", columns, schema=platform)
    op.create_table(
        "industry_taxonomies",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("standard", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("standard", "version", name="uq_industry_taxonomy_version"),
        schema=platform,
    )
    op.create_index(
        "ix_industry_taxonomies_standard", "industry_taxonomies", ["standard"], schema=platform
    )
    op.create_table(
        "industry_classifications",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("taxonomy_id", sa.String(128), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("parent_code", sa.String(128)),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("taxonomy_id", "code", name="uq_industry_classification_code"),
        schema=platform,
    )
    op.create_index(
        "ix_industry_classifications_taxonomy_id",
        "industry_classifications",
        ["taxonomy_id"],
        schema=platform,
    )
    op.create_index(
        "ix_industry_classifications_code", "industry_classifications", ["code"], schema=platform
    )
    op.create_table(
        "instrument_industry_memberships",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("instrument_id", sa.String(128), nullable=False),
        sa.Column("taxonomy_id", sa.String(128), nullable=False),
        sa.Column("industry_code", sa.String(128), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "instrument_id",
            "taxonomy_id",
            "industry_code",
            name="uq_instrument_industry_membership",
        ),
        schema=platform,
    )
    for name, columns in (
        ("ix_instrument_industry_memberships_instrument_id", ["instrument_id"]),
        ("ix_instrument_industry_memberships_taxonomy_id", ["taxonomy_id"]),
        ("ix_instrument_industry_memberships_industry_code", ["industry_code"]),
    ):
        op.create_index(name, "instrument_industry_memberships", columns, schema=platform)
    op.create_table(
        "impact_target_mappings",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("mapping_type", sa.String(24), nullable=False),
        sa.Column("mapping_code", sa.String(128), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("reviewed_by", sa.String(100)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "target_id", "mapping_type", "mapping_code", name="uq_impact_target_mapping"
        ),
        schema=analysis,
    )
    op.create_index(
        "ix_impact_target_mappings_target_id",
        "impact_target_mappings",
        ["target_id"],
        schema=analysis,
    )
    op.create_index(
        "ix_impact_target_mappings_lookup",
        "impact_target_mappings",
        ["mapping_type", "mapping_code", "status"],
        schema=analysis,
    )


def downgrade() -> None:
    bind = op.get_bind()
    platform = _schema(bind, "platform")
    analysis = _schema(bind, "analysis")
    op.drop_table("impact_target_mappings", schema=analysis)
    op.drop_table("instrument_industry_memberships", schema=platform)
    op.drop_table("industry_classifications", schema=platform)
    op.drop_table("industry_taxonomies", schema=platform)
    op.drop_table("market_instruments", schema=platform)
