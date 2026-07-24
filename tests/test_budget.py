import pytest

from app.platform.repository import InMemoryRepository
from app.workflows.budget import (
    BUDGET_DIMENSIONS,
    MVP_PROFILES,
    SOFT_THRESHOLD_RATIO,
    BudgetExceeded,
    BudgetManager,
)


def test_budget_reserve_and_settle_accumulate() -> None:
    repository = InMemoryRepository()
    budget = BudgetManager(repository)

    budget.reserve("wfr_1", "company", {"model_calls": 1, "tool_calls": 2})
    budget.settle("wfr_1", "company", {"model_calls": 1, "tool_calls": 2, "elapsed_seconds": 3})

    assert budget.used("wfr_1", "model_calls") == 2
    assert budget.used("wfr_1", "tool_calls") == 4
    assert (
        budget.remaining("wfr_1", "model_calls")
        == MVP_PROFILES["mvp_standard"].limit_for("model_calls") - 2
    )


def test_budget_hard_limit_raises_budget_exceeded() -> None:
    repository = InMemoryRepository()
    # 用极小预算配置触发硬阈值
    budget = BudgetManager(repository, MVP_PROFILES["mvp_low"])

    with pytest.raises(BudgetExceeded) as exc_info:
        budget.reserve("wfr_1", "company", {"model_calls": 100})
    assert exc_info.value.dimension == "model_calls"


def test_budget_node_limit_prevents_single_agent_monopoly() -> None:
    """节点上限防止单个 Agent 消耗整个事件预算。"""
    repository = InMemoryRepository()
    budget = BudgetManager(repository)

    # company 节点上限 model_calls=3，预留 5 应被拒
    with pytest.raises(BudgetExceeded) as exc_info:
        budget.reserve("wfr_1", "company", {"model_calls": 5})
    assert "node:company:model_calls" in exc_info.value.dimension


def test_budget_soft_limit_detection() -> None:
    repository = InMemoryRepository()
    budget = BudgetManager(repository, MVP_PROFILES["mvp_low"])
    hard = MVP_PROFILES["mvp_low"].limit_for("model_calls")
    soft_threshold = hard * SOFT_THRESHOLD_RATIO

    # 消耗到软阈值之上
    while budget.used("wfr_1", "model_calls") < soft_threshold:
        budget.settle("wfr_1", "fact_check", {"model_calls": 1})

    assert budget.is_soft_limit_reached("wfr_1", "model_calls")


def test_budget_release_on_node_failure() -> None:
    repository = InMemoryRepository()
    budget = BudgetManager(repository)

    budget.reserve("wfr_1", "company", {"model_calls": 1, "tool_calls": 2})
    budget.release("wfr_1", "company")

    # release 后余额恢复（ledger 追加，不就地覆盖）
    entries = repository.list_budget_ledger("wfr_1")
    assert any(e.entry_type == "release" for e in entries)


def test_budget_adjust_increases_remaining() -> None:
    repository = InMemoryRepository()
    budget = BudgetManager(repository, MVP_PROFILES["mvp_low"])
    hard = MVP_PROFILES["mvp_low"].limit_for("model_calls")
    budget.settle("wfr_adj", "company", {"model_calls": hard})
    assert budget.remaining("wfr_adj", "model_calls") == 0
    budget.adjust("wfr_adj", {"model_calls": 5}, reason="review_boost", actor_id="usr_1")
    assert budget.remaining("wfr_adj", "model_calls") == 5
    # 提升后应能再次预留
    budget.reserve("wfr_adj", "company", {"model_calls": 2})


def test_budget_dimensions_cover_six() -> None:
    assert set(BUDGET_DIMENSIONS) == {
        "model_calls",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "cost_minor_units",
        "elapsed_seconds",
    }

