import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.platform.db_models import Base


@pytest.fixture(autouse=True)
def _isolate_alembic_database_url(monkeypatch) -> None:
    # env.py prefers FINSIGHT_DATABASE_URL; keep migration tests on the tmp sqlite URL.
    monkeypatch.delenv("FINSIGHT_DATABASE_URL", raising=False)


def test_alembic_upgrade_and_downgrade(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")
    tables = set(inspect(create_engine(database_url)).get_table_names())

    assert "documents" in tables
    assert "events" in tables
    assert "claims" in tables
    assert "report_versions" in tables
    assert "outbox" in tables

    command.downgrade(config, "base")
    remaining = set(inspect(create_engine(database_url)).get_table_names())
    assert remaining == {"alembic_version"}


def test_alembic_adopts_complete_legacy_initial_schema(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(database_url).execution_options(
        schema_translate_map={
            "ingestion": None,
            "events": None,
            "evidence": None,
            "publishing": None,
            "platform": None,
            "analysis": None,
        }
    )
    Base.metadata.create_all(engine)

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    assert "outbox" in inspect(create_engine(database_url)).get_table_names()


def test_alembic_head_includes_research_and_brief_tables(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'research.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    tables = set(inspect(create_engine(database_url)).get_table_names())
    # 0007 新增的表
    for table in ("tool_calls", "budget_ledger", "node_attempts", "briefs"):
        assert table in tables, f"missing table: {table}"
    assert "watch_triggers" in tables
    assert "event_type_registry" in tables


def test_alembic_head_adds_review_task_resume_columns(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'review_resume.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    source_columns = {col["name"] for col in inspect(engine).get_columns("sources")}
    review_columns = {col["name"] for col in inspect(engine).get_columns("review_tasks")}
    assert "resume_from" in review_columns
    assert "blackboard_version" in review_columns
    assert "adapter_type" in source_columns
    assert "rate_limit_per_minute" in source_columns
    assert "extra_config" in source_columns
    assert "license" in source_columns
    document_columns = {col["name"] for col in inspect(engine).get_columns("documents")}
    evidence_columns = {
        col["name"] for col in inspect(engine).get_columns("evidence_spans")
    }
    assert "retention_hold" in document_columns
    assert "deleted_at" in document_columns
    assert "purged_at" in document_columns
    assert "deleted_at" in evidence_columns
    event_indexes = {idx["name"] for idx in inspect(engine).get_indexes("events")}
    assert "ix_events_occurred_at_id" in event_indexes


def test_alembic_schema_matches_orm_metadata(tmp_path) -> None:
    """迁移产生的 schema 必须与 ORM 元数据一致，避免漂移（IMP-010）。"""
    database_url = f"sqlite:///{tmp_path / 'parity.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    import sqlite3

    schema_map = {
        "ingestion": None,
        "events": None,
        "evidence": None,
        "publishing": None,
        "platform": None,
    }
    orm_engine = create_engine(database_url).execution_options(schema_translate_map=schema_map)
    orm_tables = {
        t: sorted(c["name"] for c in inspect(orm_engine).get_columns(t))
        for t in inspect(orm_engine).get_table_names()
        if not t.startswith("alembic")
    }

    conn = sqlite3.connect(str(tmp_path / "parity.db"))
    mig_tables = {
        t: sorted(r[1] for r in conn.execute(f'PRAGMA table_info("{t}")').fetchall())
        for t in sorted(
            r[0]
            for r in conn.execute(
                "select name from sqlite_master where type='table' "
                "and name not like 'sqlite_%' and name not like 'alembic%'"
            ).fetchall()
        )
    }
    conn.close()

    assert set(mig_tables) == set(orm_tables)
    for table in mig_tables:
        assert mig_tables[table] == orm_tables[table], f"column drift on {table}"
