"""工作流预算管理。

BudgetManager 为每个工作流预留、结算和释放 6 类预算维度（DD-50 §10）：
model_calls、tool_calls、input_tokens、output_tokens、cost_minor_units、elapsed_seconds。

预算配置包括：
- 软阈值：禁止扩展检索或可选节点，继续完成必需节点。
- 硬阈值：停止新调用，在安全点检查点化并进入 waiting_review 或事实卡片降级。
- 节点上限：防止单个 Agent 消耗整个事件预算。

余额通过账本汇总（reserve/settle/release/adjust 追加写），不就地覆盖消费量。
预算提升属于人工审核决定，必须记录操作者、原因和新增额度。
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.domain import BudgetLedgerEntry
from app.platform.ids import new_id

BUDGET_DIMENSIONS = (
    "model_calls",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "cost_minor_units",
    "elapsed_seconds",
)

# 软阈值比例：达到硬阈值的 80% 时停止扩展检索/可选节点
SOFT_THRESHOLD_RATIO = 0.80


class BudgetExceeded(ValueError):
    """预算硬阈值耗尽。"""

    def __init__(self, dimension: str, limit: int, used: int) -> None:
        self.dimension = dimension
        self.limit = limit
        self.used = used
        super().__init__(f"BUDGET_HARD_LIMIT: dimension={dimension} used={used} limit={limit}")


class BudgetSoftLimitReached(ValueError):
    """预算软阈值达到，停止扩展检索或可选节点。"""

    def __init__(self, dimension: str) -> None:
        self.dimension = dimension
        super().__init__(f"BUDGET_SOFT_LIMIT: {dimension}")


@dataclass(frozen=True)
class BudgetProfile:
    """预算配置：每维度硬阈值与节点上限。"""

    name: str
    limits: dict[str, int] = field(default_factory=dict)
    node_limits: dict[str, dict[str, int]] = field(default_factory=dict)

    def limit_for(self, dimension: str) -> int:
        return self.limits.get(dimension, 0)

    def node_limit_for(self, node: str, dimension: str) -> Optional[int]:
        return self.node_limits.get(node, {}).get(dimension)


# MVP 默认预算配置：单事件硬阈值
MVP_PROFILES: dict[str, BudgetProfile] = {
    "mvp_standard": BudgetProfile(
        name="mvp_standard",
        limits={
            "model_calls": 20,
            "tool_calls": 40,
            "input_tokens": 200_000,
            "output_tokens": 50_000,
            "cost_minor_units": 500,  # 以分为单位，约 5 USD
            "elapsed_seconds": 300,
        },
        node_limits={
            "company": {"model_calls": 3, "tool_calls": 5},
            "fact_check": {"model_calls": 3, "tool_calls": 8},
            "skeptic": {"model_calls": 3, "tool_calls": 5},
            "synthesize": {"model_calls": 2, "tool_calls": 0},
        },
    ),
    "mvp_low": BudgetProfile(
        name="mvp_low",
        limits={
            "model_calls": 8,
            "tool_calls": 15,
            "input_tokens": 50_000,
            "output_tokens": 15_000,
            "cost_minor_units": 150,
            "elapsed_seconds": 120,
        },
        node_limits={
            "company": {"model_calls": 2, "tool_calls": 3},
            "fact_check": {"model_calls": 2, "tool_calls": 4},
        },
    ),
}


class BudgetManager:
    """工作流预算账本：预留、结算、释放与阈值检查。"""

    def __init__(self, repository, profile: Optional[BudgetProfile] = None) -> None:
        self.repository = repository
        self.profile = profile or MVP_PROFILES["mvp_standard"]

    def reserve(
        self,
        workflow_id: str,
        node_name: str,
        amounts: dict[str, int],
    ) -> BudgetLedgerEntry:
        """节点启动前预留预算。超硬阈值或节点上限则抛 BudgetExceeded。"""
        for dimension, amount in amounts.items():
            self._check_hard_limit(workflow_id, node_name, dimension, amount)
            self._check_node_limit(node_name, dimension, amount)
        # 预留多笔合并为一条记录
        total = sum(amounts.values())
        entry = BudgetLedgerEntry(
            id=new_id("blg"),
            workflow_id=workflow_id,
            node_name=node_name,
            dimension=",".join(f"{k}={v}" for k, v in amounts.items()),
            entry_type="reserve",
            amount=total,
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_budget_ledger(entry)
        return entry

    def settle(
        self,
        workflow_id: str,
        node_name: str,
        actual: dict[str, int],
    ) -> BudgetLedgerEntry:
        """节点结束后按实际值结算。"""
        total = sum(actual.values())
        entry = BudgetLedgerEntry(
            id=new_id("blg"),
            workflow_id=workflow_id,
            node_name=node_name,
            dimension=",".join(f"{k}={v}" for k, v in actual.items()),
            entry_type="settle",
            amount=total,
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_budget_ledger(entry)
        return entry

    def release(self, workflow_id: str, node_name: str) -> BudgetLedgerEntry:
        """释放未结算的预留（节点失败时）。"""
        entry = BudgetLedgerEntry(
            id=new_id("blg"),
            workflow_id=workflow_id,
            node_name=node_name,
            dimension="all",
            entry_type="release",
            amount=0,
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_budget_ledger(entry)
        return entry

    def adjust(
        self,
        workflow_id: str,
        amounts: dict[str, int],
        *,
        reason: str,
        actor_id: Optional[str] = None,
    ) -> BudgetLedgerEntry:
        """人工审核提升预算额度。amount 为正表示增加可用额度（记为负消耗）。"""
        if not amounts or any(v <= 0 for v in amounts.values()):
            raise ValueError("BUDGET_ADJUST_INVALID")
        # 维度编码附带 reason/actor，便于审计回放
        meta = f"reason={reason}"
        if actor_id:
            meta = f"{meta},actor={actor_id}"
        encoded = ",".join(f"{k}={v}" for k, v in amounts.items())
        entry = BudgetLedgerEntry(
            id=new_id("blg"),
            workflow_id=workflow_id,
            node_name=None,
            dimension=f"{encoded}|{meta}",
            entry_type="adjust",
            amount=sum(amounts.values()),
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_budget_ledger(entry)
        return entry

    def used(self, workflow_id: str, dimension: str) -> int:
        """某维度已消耗量（settle 与 reserve 之和，adjust 记为额度贷记）。"""
        entries = self.repository.list_budget_ledger(workflow_id)
        total = 0
        for entry in entries:
            dim = entry.dimension.split("|", 1)[0]
            if entry.entry_type == "settle" and dimension in dim:
                total += self._extract_dimension(dim, dimension)
            elif entry.entry_type == "reserve" and dimension in dim:
                total += self._extract_dimension(dim, dimension)
            elif entry.entry_type == "adjust" and dimension in dim:
                total -= self._extract_dimension(dim, dimension)
            elif entry.entry_type == "release":
                continue
        return total

    def remaining(self, workflow_id: str, dimension: str) -> int:
        return self.profile.limit_for(dimension) - self.used(workflow_id, dimension)

    def is_soft_limit_reached(self, workflow_id: str, dimension: str) -> bool:
        used = self.used(workflow_id, dimension)
        hard = self.profile.limit_for(dimension)
        if hard == 0:
            return False
        return used >= hard * SOFT_THRESHOLD_RATIO

    def _check_hard_limit(
        self, workflow_id: str, node_name: str, dimension: str, amount: int
    ) -> None:
        hard = self.profile.limit_for(dimension)
        if hard == 0:
            return
        used = self.used(workflow_id, dimension)
        if used + amount > hard:
            raise BudgetExceeded(dimension=dimension, limit=hard, used=used)

    def _check_node_limit(self, node_name: str, dimension: str, amount: int) -> None:
        node_limit = self.profile.node_limit_for(node_name, dimension)
        if node_limit is None:
            return
        if amount > node_limit:
            raise BudgetExceeded(
                dimension=f"node:{node_name}:{dimension}", limit=node_limit, used=0
            )

    @staticmethod
    def _extract_dimension(encoded: str, dimension: str) -> int:
        """从 'model_calls=3,tool_calls=2' 中提取指定维度值。"""
        for part in encoded.split(","):
            if "=" in part:
                key, value = part.split("=", 1)
                if key.strip() == dimension:
                    try:
                        return int(value)
                    except ValueError:
                        return 0
        return 0


def elapsed_since(started: float) -> int:
    """从 perf_counter 起点计算已耗秒数。"""
    return int(time.perf_counter() - started)
