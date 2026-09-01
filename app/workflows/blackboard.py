"""Blackboard 字段写入所有权。

Blackboard 是版本化结构化状态，每个字段有唯一写入者，跨节点数据通过 Schema 校验
（DD-50 §7）。更新必须带 ``expected_state_version`` 和字段所有者身份，Repository
拒绝非所有者写入及对已提交节点结果的就地修改。

LangGraph 的 StateGraph 节点返回 dict 时会合并状态；本模块在节点层加所有权校验，
确保只有声明的节点能写对应字段，并通过 ``state_version`` 实现乐观锁。
"""

from typing import Any

# 字段 -> 唯一写入者节点（DD-50 §7 所有权表；DD-80 增加动态研究字段）
FIELD_OWNERS: dict[str, str] = {
    "event_snapshot": "context",
    "fact_check_snapshot": "fact_check",
    "company_analysis": "company",
    "counter_analysis": "skeptic",
    "synthesis": "synthesize",
    "research_memo": "research_writer",
    "research_pack": "research_writer",
    "guardrail_result": "guardrail",
    "report_draft_ref": "draft",
    "research_plan": "planner",
    "task_outputs": "dynamic_engine",
    "plan_status": "dynamic_engine",
}


def owner_for_field(field: str) -> str | None:
    """返回字段的注册所有者；支持 task_outputs.{task_name} 子字段约定。"""
    if field.startswith("task_outputs."):
        return "dynamic_engine"
    return FIELD_OWNERS.get(field)


class BlackboardVersionConflict(ValueError):
    """Blackboard 版本冲突：expected_state_version 与当前不匹配。"""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"BLACKBOARD_VERSION_CONFLICT: expected_state_version={expected} actual={actual}"
        )


class BlackboardOwnershipError(PermissionError):
    """节点试图写入非自己拥有的字段。"""

    def __init__(self, node: str, field: str, owner: str) -> None:
        self.node = node
        self.field = field
        self.owner = owner
        super().__init__(
            f"BLACKBOARD_OWNERSHIP_VIOLATION: node={node} cannot write field={field} "
            f"(owner={owner})"
        )


class BlackboardGuard:
    """校验节点写入的字段所有权与版本一致性。"""

    def __init__(self, repository) -> None:
        self.repository = repository

    def validate_write(
        self,
        workflow_id: str,
        node: str,
        updates: dict[str, Any],
        expected_state_version: int,
    ) -> None:
        run = self.repository.get_workflow_run(workflow_id)
        actual_version = run.state_version if run else 0
        if actual_version != expected_state_version:
            raise BlackboardVersionConflict(expected_state_version, actual_version)
        for field in updates:
            owner = FIELD_OWNERS.get(field)
            if owner is None:
                continue  # 未登记字段不强制所有权（如 workflow_id 等元数据）
            if owner != node:
                raise BlackboardOwnershipError(node, field, owner)

    def next_state_version(self, workflow_id: str) -> int:
        run = self.repository.get_workflow_run(workflow_id)
        return (run.state_version if run else 0) + 1
