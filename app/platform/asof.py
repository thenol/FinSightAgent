"""as_of 时间截面与未来数据防护。

研究与评估查询只能读取 ``published_at <= as_of`` 且当时已被系统获取
（``ingested_at <= as_of``）的数据（DD-02 §4、IMP-012）。后续公告修订和未来
行情不得进入历史工作流，否则回测会产生未来信息泄漏。

本模块提供：
- ``visible_as_of``：判断一个带时间戳的对象在 as_of 时点是否可见。
- ``AsOfViolation``：工具网关与研究查询拒绝越界结果时的错误类型。
- ``ensure_within_as_of``：供 ToolGateway 校验工具返回数据是否越界。
"""

from datetime import datetime, timezone
from typing import Any, Optional, Protocol


class Timestamped(Protocol):
    published_at: Optional[datetime]
    ingested_at: Optional[datetime]
    created_at: Optional[datetime]


class AsOfViolation(ValueError):
    """数据在指定 as_of 时点尚不可见，禁止进入历史工作流。"""


def visible_as_of(item: Any, as_of: Optional[datetime]) -> bool:
    """判断 item 在 as_of 时点是否可见。

    as_of 为 None 时不做过滤（兼容默认行为）。否则检查对象上所有已知的时间戳
    属性，任一越界即不可见：published_at、ingested_at、created_at、occurred_at、
    as_of。不同对象使用不同的主时间字段（Event 用 occurred_at，Claim/FactCard
    用 as_of，DocumentRevision 用 created_at），全部覆盖以避免遗漏。缺失的
    时间戳视为满足（不因未知而拒绝）。
    """
    if as_of is None:
        return True
    cutoff = _to_utc(as_of)
    for attr in ("published_at", "ingested_at", "created_at", "occurred_at", "as_of"):
        value = getattr(item, attr, None)
        if value is None:
            continue
        if _to_utc(value) > cutoff:
            return False
    return True


def ensure_within_as_of(item: Any, as_of: Optional[datetime], *, context: str = "") -> None:
    """校验 item 在 as_of 时点可见，否则抛 AsOfViolation。供 ToolGateway 使用。"""
    if as_of is None:
        return
    if not visible_as_of(item, as_of):
        identifier = getattr(item, "id", None) or repr(item)
        raise AsOfViolation(
            f"AS_OF_VIOLATION: item {identifier} not visible as of {as_of.isoformat()}"
            + (f" ({context})" if context else "")
        )


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def default_as_of(now: Optional[datetime] = None) -> datetime:
    """返回默认 as_of（当前 UTC 时间），用于不显式传 as_of 的调用。"""
    return now or datetime.now(timezone.utc)
