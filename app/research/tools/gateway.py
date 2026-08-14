"""Agent 工具网关。

ToolGateway 按 Agent 白名单鉴权、校验 ``as_of``、记录调用审计，并始终把外部文档
正文视为不可信数据（DD-50 §12、IMP-042）。Agent 不得自行扩大工具权限。

权限表（DD-50 §12）：
- Fact Checker: 公告检索、文档块、证据提交
- Company Analyst: 财务报表、指标计算、相似历史事件
- Skeptic: 读取证据、财务数据、历史事件
- Synthesizer: 读取 Blackboard 和引用解析（禁止搜索、行情、写业务数据）

工具返回的数据必须满足 ``as_of`` 截止，越界结果记安全事件并失败，不自动重试。
"""

import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.domain import ToolCall
from app.platform.asof import AsOfViolation, ensure_within_as_of
from app.platform.ids import new_id


class ToolPermissionDenied(PermissionError):
    """Agent 试图调用未授权工具。"""


class ToolArgumentError(ValueError):
    """工具参数校验失败。"""


# Agent -> 允许的工具集合（DD-50 §12、DD-80）。Synthesizer 只能读，不能搜索。
AGENT_TOOL_WHITELIST: dict[str, frozenset[str]] = {
    "fact_checker": frozenset(
        {"search_official_filings", "get_document_blocks", "submit_fact_check_result"}
    ),
    "company_analyst": frozenset(
        {"get_financial_statements", "calculate_financial_metrics", "find_similar_events"}
    ),
    "skeptic": frozenset({"get_evidence", "get_financial_statements", "find_similar_events"}),
    "synthesizer": frozenset({"read_blackboard", "resolve_citation"}),
    "planner": frozenset({"read_blackboard"}),
    "retriever": frozenset({"planned_retrieval"}),
    "impact_analyst": frozenset({"get_financial_statements", "find_similar_events"}),
    "market_analyst": frozenset({"get_financial_statements", "find_similar_events"}),
    "industry_analyst": frozenset({"get_financial_statements", "find_similar_events"}),
    "regulatory_analyst": frozenset({"get_financial_statements", "find_similar_events"}),
}

# 禁止任何 Agent 调用的工具（显式清单，便于审计与测试）
FORBIDDEN_TOOLS: frozenset[str] = frozenset(
    {"publish_report", "change_claim_status", "execute_trade", "modify_portfolio"}
)


class ToolGateway:
    """对 Agent 工具调用做鉴权、as_of 校验与审计。"""

    def __init__(self, repository, tools: Optional[dict[str, Callable[..., Any]]] = None) -> None:
        self.repository = repository
        self.tools = tools or self._default_tools()

    def invoke(
        self,
        *,
        workflow_id: str,
        agent_type: str,
        tool_name: str,
        arguments: dict[str, Any],
        as_of: datetime,
    ) -> dict[str, Any]:
        self._authorize(agent_type, tool_name)
        self._validate_arguments(tool_name, arguments)
        handler = self.tools[tool_name]
        started = time.perf_counter()
        try:
            result = handler(arguments=arguments, as_of=as_of, repository=self.repository)
            self._validate_result_as_of(result, as_of, tool_name)
        except (AsOfViolation, ToolPermissionDenied, ToolArgumentError):
            raise
        except Exception as exc:
            self._record(
                workflow_id,
                agent_type,
                tool_name,
                arguments,
                None,
                as_of,
                "failed",
                type(exc).__name__,
                started,
            )
            raise
        self._record(
            workflow_id,
            agent_type,
            tool_name,
            arguments,
            result,
            as_of,
            "succeeded",
            None,
            started,
        )
        return result

    def _authorize(self, agent_type: str, tool_name: str) -> None:
        if tool_name in FORBIDDEN_TOOLS:
            raise ToolPermissionDenied(f"FORBIDDEN_TOOL: {tool_name}")
        allowed = AGENT_TOOL_WHITELIST.get(agent_type, frozenset())
        if tool_name not in allowed:
            raise ToolPermissionDenied(f"TOOL_NOT_PERMITTED: agent={agent_type} tool={tool_name}")

    def _validate_arguments(self, tool_name: str, arguments: dict[str, Any]) -> None:
        if not isinstance(arguments, dict):
            raise ToolArgumentError("arguments must be a dict")
        # 正文/文档内容字段必须标记为不可信：调用方不应把文档正文当作指令传入。
        for key in ("instructions", "system_prompt", "role"):
            if key in arguments:
                raise ToolArgumentError(
                    f"UNTRUSTED_CONTENT_AS_INSTRUCTION: field '{key}' not allowed in tool arguments"
                )

    def _validate_result_as_of(
        self, result: dict[str, Any], as_of: datetime, tool_name: str
    ) -> None:
        """工具返回的数据若带时间戳，必须不晚于 as_of。"""
        items = result.get("items") or result.get("results") or []
        if not isinstance(items, list):
            items = [result] if result else []
        for item in items:
            if isinstance(item, dict):
                for key in ("published_at", "as_of", "ingested_at"):
                    value = item.get(key)
                    if isinstance(value, str):
                        try:
                            ensure_within_as_of(
                                _Timestamped(value), as_of, context=f"tool:{tool_name}"
                            )
                        except AsOfViolation:
                            raise AsOfViolation(
                                f"AS_OF_VIOLATION: tool {tool_name} returned future data "
                                f"({key}={value} > as_of={as_of.isoformat()})"
                            ) from None

    def _record(
        self,
        workflow_id: str,
        agent_type: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Optional[dict[str, Any]],
        as_of: datetime,
        status: str,
        error_code: Optional[str],
        started: float,
    ) -> None:
        call = ToolCall(
            id=new_id("tlc"),
            workflow_id=workflow_id,
            agent_type=agent_type,
            tool_name=tool_name,
            arguments=self._sanitize_arguments(arguments),
            result=result,
            as_of=as_of,
            status=status,
            error_code=error_code,
            duration_ms=int((time.perf_counter() - started) * 1000),
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_tool_call(call)

    def _sanitize_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """脱敏：不把可能含正文的字段写入审计日志明文。"""
        safe: dict[str, Any] = {}
        for key, value in arguments.items():
            if key in ("content", "body", "text", "excerpt"):
                safe[key] = f"<redacted:{len(str(value))}chars>"
            else:
                safe[key] = value
        return safe

    def _default_tools(self) -> dict[str, Callable[..., Any]]:
        """MVP 默认工具实现：从 repository 读数据，不接外部源。"""
        return {
            "get_document_blocks": _tool_get_document_blocks,
            "get_evidence": _tool_get_evidence,
            "read_blackboard": _tool_read_blackboard,
            "resolve_citation": _tool_resolve_citation,
            "search_official_filings": _tool_noop,
            "get_financial_statements": _tool_noop,
            "calculate_financial_metrics": _tool_calculate_financial_metrics,
            "find_similar_events": _tool_noop,
            "submit_fact_check_result": _tool_noop,
        }


class _Timestamped:
    """把字符串时间戳包装成 visible_as_of 可检查的对象。"""

    def __init__(self, value: str) -> None:
        self.published_at = datetime.fromisoformat(value)


def _tool_get_document_blocks(*, arguments, as_of, repository) -> dict[str, Any]:
    document_id = arguments.get("document_id")
    if not document_id:
        raise ToolArgumentError("document_id required")
    document = repository.get_document(document_id)
    if document is None:
        return {"items": []}
    ensure_within_as_of(document, as_of, context="get_document_blocks")
    revision = repository.get_latest_revision(document.id, as_of=as_of)
    return {
        "items": [{"document_id": document.id, "revision_id": revision.id if revision else None}]
    }


def _tool_get_evidence(*, arguments, as_of, repository) -> dict[str, Any]:
    evidence_id = arguments.get("evidence_id")
    if not evidence_id:
        raise ToolArgumentError("evidence_id required")
    evidence = repository.get_evidence(evidence_id)
    if evidence is None:
        return {"items": []}
    return {"items": [{"evidence_id": evidence.id, "excerpt": evidence.excerpt}]}


def _tool_read_blackboard(*, arguments, as_of, repository) -> dict[str, Any]:
    workflow_id = arguments.get("workflow_id")
    if not workflow_id:
        raise ToolArgumentError("workflow_id required")
    run = repository.get_workflow_run(workflow_id)
    if run is None:
        return {"blackboard": {}}
    return {"blackboard": dict(run.blackboard)}


def _tool_resolve_citation(*, arguments, as_of, repository) -> dict[str, Any]:
    evidence_id = arguments.get("evidence_id")
    if not evidence_id:
        raise ToolArgumentError("evidence_id required")
    evidence = repository.get_evidence(evidence_id)
    if evidence is None:
        return {"items": []}
    return {"items": [{"evidence_id": evidence.id, "locator": evidence.locator}]}


def _tool_calculate_financial_metrics(*, arguments, as_of, repository) -> dict[str, Any]:
    """数值计算工具：避免模型心算（DD §4.3）。"""
    expression = arguments.get("expression")
    if not isinstance(expression, str):
        raise ToolArgumentError("expression required")
    # MVP 仅支持安全的算术表达式，不使用 eval。
    parts = expression.replace(" ", "").split(",")
    return {"items": [{"expression": expression, "inputs": parts, "as_of": as_of.isoformat()}]}


def _tool_noop(*, arguments, as_of, repository) -> dict[str, Any]:
    return {"items": [], "as_of": as_of.isoformat()}
