"""局部重跑失效传播（DD-50 §14）。

变化类型映射到最小失效节点集合；清除对应 Blackboard 字段并使已成功
NodeAttempt 失效，迫使下游以新 input_hash / 新 attempt 重跑。
"""

from __future__ import annotations

from collections.abc import Iterable

# 节点执行顺序（用于 resume_from 截断）
NODE_ORDER = (
    "context",
    "fact_check",
    "company",
    "skeptic",
    "synthesize",
    "draft",
    "guardrail",
)

# 节点写入的 Blackboard 字段
NODE_OUTPUT_FIELDS: dict[str, tuple[str, ...]] = {
    "context": ("event_snapshot",),
    "fact_check": ("fact_check_snapshot",),
    "company": ("company_analysis",),
    "skeptic": ("counter_analysis",),
    "synthesize": ("synthesis",),
    "draft": ("report_draft_ref", "report_draft"),
    "guardrail": ("guardrail_result",),
}

# DD-50 §14 变化 → 最小失效节点
INVALIDATION_TRIGGERS: dict[str, tuple[str, ...]] = {
    "claim_changed": ("company", "skeptic", "synthesize", "draft", "guardrail"),
    "finance_changed": ("company", "skeptic", "synthesize", "draft", "guardrail"),
    "company_returned": ("company", "skeptic", "synthesize", "draft", "guardrail"),
    "guardrail_policy": ("draft", "guardrail"),
    "budget_resume": (),  # 不失效已成功节点，靠幂等复用后继续
    "downgrade_fact_only": ("synthesize", "draft", "guardrail"),
    "explicit_retry": (),  # failed 显式重试：不主动清字段
}


def nodes_to_invalidate(trigger: str, resume_from: str | None = None) -> list[str]:
    """返回应按触发类型失效的节点列表；若指定 resume_from，并入该节点及下游。"""
    base = list(INVALIDATION_TRIGGERS.get(trigger, ()))
    if resume_from:
        if resume_from not in NODE_ORDER:
            raise ValueError(f"UNKNOWN_RESUME_FROM:{resume_from}")
        start = NODE_ORDER.index(resume_from)
        for node in NODE_ORDER[start:]:
            if node not in base:
                base.append(node)
    # 保持拓扑顺序
    order = {name: idx for idx, name in enumerate(NODE_ORDER)}
    return sorted(set(base), key=lambda n: order.get(n, 99))


def fields_to_clear(nodes: Iterable[str]) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        for field in NODE_OUTPUT_FIELDS.get(node, ()):
            if field not in seen:
                seen.add(field)
                fields.append(field)
    return fields


def apply_invalidation(blackboard: dict, nodes: Iterable[str]) -> dict:
    """返回清除失效字段后的 Blackboard 副本。"""
    cleared = dict(blackboard)
    for field in fields_to_clear(nodes):
        cleared.pop(field, None)
    return cleared
