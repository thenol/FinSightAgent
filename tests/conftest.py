"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _force_memory_repository(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Keep create_app() on InMemoryRepository regardless of developer shell env.

    Several API suites seed data via repository.save_* helpers that exist on the
    memory adapter. A local FINSIGHT_REPOSITORY=postgresql export would otherwise
    make those tests fail with AttributeError / IntegrityError noise.
    PostgreSQL integration tests opt out with the ``postgres`` marker.
    """
    if request.node.get_closest_marker("postgres"):
        return
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    monkeypatch.setenv("FINSIGHT_WORKFLOW_AUTO_TRIGGER_ENABLED", "false")
