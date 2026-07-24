"""Durable workflow recovery contracts."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import TypedDict
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.platform.repository import SqlAlchemyRepository
from app.worker import claim_workflow_run
from app.workflows.checkpoints import (
    MemoryCheckpointerFactory,
    PostgresCheckpointerFactory,
    checkpointer_factory_for,
)
from app.workflows.service import ResearchState, WorkflowService

SCHEMA_MAP = {
    "ingestion": None,
    "events": None,
    "evidence": None,
    "publishing": None,
    "platform": None,
}


class CountingFactory:
    def __init__(self) -> None:
        self.calls = 0
        self.saver = MemorySaver()

    def create(self):
        self.calls += 1
        return self.saver


class InterruptingWorkflowService(WorkflowService):
    """Small graph that crashes after committing one externally visible effect."""

    def __init__(self, repository, *, interrupt: bool) -> None:
        self.interrupt = interrupt
        super().__init__(repository)

    def _build_graph(self):
        graph = StateGraph(ResearchState)
        graph.add_node("report", self._report)
        graph.add_node("finish", self._finish)
        graph.add_edge(START, "report")
        graph.add_edge("report", "finish")
        graph.add_edge("finish", END)
        return graph.compile(checkpointer=self.checkpointer_factory.create())

    def _report(self, state: ResearchState) -> dict:
        def emit_report(_state: ResearchState) -> dict:
            with self.repository.transaction() as repository:
                repository.add_outbox(
                    "report.created.v1",
                    state["workflow_id"],
                    {"workflow_id": state["workflow_id"]},
                )
            return {"event_snapshot": {"reported": True}}

        return self._execute_node(
            "report",
            emit_report,
            state,
            input_fields=(),
            reserve_amounts={"model_calls": 0, "tool_calls": 0, "elapsed_seconds": 1},
        )

    def _finish(self, state: ResearchState) -> dict:
        def finish(_state: ResearchState) -> dict:
            if self.interrupt:
                raise KeyboardInterrupt("simulated worker termination")
            return {"synthesis": {"status": "complete"}}

        return self._execute_node(
            "finish",
            finish,
            state,
            input_fields=("event_snapshot",),
            reserve_amounts={"model_calls": 0, "tool_calls": 0, "elapsed_seconds": 1},
        )


def _sqlite_repository(database_url: str) -> SqlAlchemyRepository:
    repository = SqlAlchemyRepository(database_url, schema_translate_map=SCHEMA_MAP)
    repository.create_schema_for_tests()
    return repository


def test_checkpointer_factory_is_injectable(tmp_path) -> None:
    repository = _sqlite_repository(f"sqlite:///{tmp_path / 'factory.db'}")
    factory = CountingFactory()

    WorkflowService(repository, checkpointer_factory=factory)

    assert factory.calls == 1
    assert isinstance(checkpointer_factory_for(repository), MemoryCheckpointerFactory)


def test_sqlite_restart_reuses_completed_attempt_without_duplicate_report(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'recovery.db'}"
    first_repository = _sqlite_repository(database_url)
    first_service = InterruptingWorkflowService(first_repository, interrupt=True)
    run = first_service.create("evt_recovery", "test", datetime.now(timezone.utc))

    with pytest.raises(KeyboardInterrupt, match="simulated worker termination"):
        first_service.run(run.id)

    interrupted = first_repository.get_workflow_run(run.id)
    assert interrupted is not None
    assert interrupted.status == "running"
    assert len(first_repository.list_outbox()) == 1

    restarted_repository = _sqlite_repository(database_url)
    restarted_service = InterruptingWorkflowService(restarted_repository, interrupt=False)
    recovered = restarted_service.run(run.id)

    assert recovered.status == "succeeded"
    assert len(restarted_repository.list_outbox()) == 1
    report_attempts = restarted_repository.list_node_attempts(run.id, "report")
    assert len(report_attempts) == 1
    assert report_attempts[0].status == "succeeded"


def test_claiming_refuses_non_postgresql_repository(tmp_path) -> None:
    repository = _sqlite_repository(f"sqlite:///{tmp_path / 'claim.db'}")

    with pytest.raises(RuntimeError, match="PostgreSQL advisory locks"):
        with claim_workflow_run(repository, stale_after=timedelta(seconds=1)):
            pass


class RecoveryState(TypedDict, total=False):
    value: str


@pytest.mark.skipif(
    not os.getenv("FINSIGHT_TEST_POSTGRES_URL"),
    reason="requires FINSIGHT_TEST_POSTGRES_URL integration database",
)
def test_postgres_checkpoint_survives_new_connection() -> None:
    from langgraph.checkpoint.postgres import PostgresSaver

    database_url = os.environ["FINSIGHT_TEST_POSTGRES_URL"]
    factory = PostgresCheckpointerFactory(database_url)
    first_graph_builder = StateGraph(RecoveryState)
    first_graph_builder.add_node("write", lambda _state: {"value": "persisted"})
    first_graph_builder.add_edge(START, "write")
    first_graph_builder.add_edge("write", END)
    thread_id = f"integration-{uuid4()}"
    first_graph = first_graph_builder.compile(checkpointer=factory.create())
    first_graph.invoke({}, {"configurable": {"thread_id": thread_id}})

    with PostgresSaver.from_conn_string(factory.database_url) as restarted_saver:
        restarted_saver.setup()
        second_graph_builder = StateGraph(RecoveryState)
        second_graph_builder.add_node("write", lambda _state: {"value": "unexpected"})
        second_graph_builder.add_edge(START, "write")
        second_graph_builder.add_edge("write", END)
        second_graph = second_graph_builder.compile(checkpointer=restarted_saver)
        snapshot = second_graph.get_state({"configurable": {"thread_id": thread_id}})

    assert snapshot.values["value"] == "persisted"
