"""工作流节点错误分类与重试策略（DD-50 §16）。"""

from __future__ import annotations

import random
from typing import Callable

from pydantic import ValidationError

from app.platform.asof import AsOfViolation
from app.workflows.budget import BudgetExceeded

# 瞬时模型/网络错误：节点内指数退避，最多 2 次重试（共 3 次尝试）
MODEL_TRANSIENT_MAX_RETRIES = 2
# Schema 校验失败：带错误修复再试 1 次
OUTPUT_SCHEMA_MAX_RETRIES = 1

TRANSIENT_ERROR_NAMES = frozenset(
    {
        "TimeoutError",
        "ConnectionError",
        "ConnectionResetError",
        "BrokenPipeError",
        "OSError",
        "MODEL_TRANSIENT",
        "TransientModelError",
    }
)

NON_RETRYABLE = frozenset(
    {
        "TOOL_AS_OF_VIOLATION",
        "BUDGET_HARD_LIMIT",
        "POLICY_VIOLATION",
        "BLACKBOARD_VERSION_CONFLICT",
        "BLACKBOARD_OWNERSHIP_VIOLATION",
        "TOOL_PERMISSION_DENIED",
        "TOOL_ARGUMENT_ERROR",
    }
)


class TransientModelError(RuntimeError):
    """可重试的瞬时模型/网络错误。"""

    def __init__(self, message: str = "MODEL_TRANSIENT") -> None:
        super().__init__(message)


class OutputSchemaInvalid(ValueError):
    """Agent 输出不符合 Schema，可修复重试一次。"""

    def __init__(self, message: str = "OUTPUT_SCHEMA_INVALID") -> None:
        super().__init__(message)


class PolicyViolation(ValueError):
    """策略违规，阻止报告并进入审核。"""

    def __init__(self, message: str = "POLICY_VIOLATION") -> None:
        super().__init__(message)


def classify_error(exc: BaseException) -> str:
    """将异常映射为稳定的错误码。"""
    if isinstance(exc, BudgetExceeded):
        return "BUDGET_HARD_LIMIT"
    if isinstance(exc, AsOfViolation):
        return "TOOL_AS_OF_VIOLATION"
    if isinstance(exc, OutputSchemaInvalid) or isinstance(exc, ValidationError):
        return "OUTPUT_SCHEMA_INVALID"
    if isinstance(exc, PolicyViolation):
        return "POLICY_VIOLATION"
    if isinstance(exc, TransientModelError):
        return "MODEL_TRANSIENT"
    name = type(exc).__name__
    if name in TRANSIENT_ERROR_NAMES or str(exc).startswith("MODEL_TRANSIENT"):
        return "MODEL_TRANSIENT"
    code = getattr(exc, "error_code", None)
    if isinstance(code, str) and code:
        return code
    if name in NON_RETRYABLE:
        return name
    # 未知执行错误：不自动重试，避免放大副作用
    return "NODE_EXECUTION_ERROR"


def max_retries_for(error_code: str) -> int:
    if error_code == "MODEL_TRANSIENT":
        return MODEL_TRANSIENT_MAX_RETRIES
    if error_code == "OUTPUT_SCHEMA_INVALID":
        return OUTPUT_SCHEMA_MAX_RETRIES
    return 0


def compute_backoff_seconds(attempt_index: int, *, jitter: bool = True) -> float:
    """指数退避：0.05 * 2^attempt，可选 jitter。测试可注入 sleep 跳过真实等待。"""
    base = 0.05 * (2**attempt_index)
    if jitter:
        base += random.uniform(0, 0.05)
    return min(base, 2.0)


def should_retry(error_code: str, failed_attempts: int) -> bool:
    """failed_attempts 为已失败次数（含本次）；返回是否还应再试。"""
    if error_code in NON_RETRYABLE:
        return False
    return failed_attempts <= max_retries_for(error_code)


SleepFn = Callable[[float], None]


def default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
