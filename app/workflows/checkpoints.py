"""LangGraph checkpointer construction for development and PostgreSQL deployments."""

from __future__ import annotations

from threading import Lock
from typing import Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver


class CheckpointerFactory(Protocol):
    """Creates the checkpointer used when compiling a workflow graph."""

    def create(self) -> BaseCheckpointSaver: ...


class MemoryCheckpointerFactory:
    """Process-local checkpointer for memory and SQLite repositories."""

    def __init__(self, saver: BaseCheckpointSaver | None = None) -> None:
        self._saver = saver or MemorySaver()

    def create(self) -> BaseCheckpointSaver:
        return self._saver


_postgres_lock = Lock()
_postgres_savers: dict[str, BaseCheckpointSaver] = {}


class PostgresCheckpointerFactory:
    """Creates a process-shared PostgresSaver backed by a connection pool."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _psycopg_url(database_url)

    def create(self) -> BaseCheckpointSaver:
        with _postgres_lock:
            cached = _postgres_savers.get(self.database_url)
            if cached is not None:
                return cached

            # Imports stay optional for memory-only installations.
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                self.database_url,
                min_size=1,
                max_size=10,
                open=True,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
            )
            saver = PostgresSaver(pool)
            try:
                saver.setup()
            except BaseException:
                pool.close()
                raise
            _postgres_savers[self.database_url] = saver
            return saver


def checkpointer_factory_for(repository) -> CheckpointerFactory:
    """Select durable checkpoints only when the repository is PostgreSQL."""

    engine = getattr(repository, "engine", None)
    if engine is None or engine.dialect.name != "postgresql":
        return MemoryCheckpointerFactory()
    database_url = engine.url.render_as_string(hide_password=False)
    return PostgresCheckpointerFactory(database_url)


def _psycopg_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+psycopg://")
    return database_url
