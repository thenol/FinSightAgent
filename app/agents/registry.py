"""Specialist Agent Registry（DD-80 §5）。

AgentRegistry 以声明式方式管理 Specialist Agent 的能力、输入/输出 Schema、
允许工具、预算配置和质量门。运行时不允许 Agent 自行扩大权限。
"""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from app.domain import AgentRegistration

DEFAULT_REGISTRATIONS: list[AgentRegistration] = [
    AgentRegistration(
        agent_key="fact_checker",
        version="1.0.0",
        display_name="事实核验 Agent",
        capabilities=["fact_verify"],
        input_schema_refs=["event-snapshot/1.0.0", "claim-list/1.0.0"],
        output_schema_ref="fact-check-snapshot/1.0.0",
        allowed_tools=[
            "search_official_filings",
            "get_document_blocks",
            "submit_fact_check_result",
        ],
        budget_profile="mvp_standard",
    ),
    AgentRegistration(
        agent_key="company_analyst",
        version="1.0.0",
        display_name="公司基本面分析 Agent",
        capabilities=["company_analyze"],
        input_schema_refs=["fact-check-snapshot/1.0.0", "event-snapshot/1.0.0"],
        output_schema_ref="company-analysis-output/1.0.0",
        allowed_tools=[
            "get_financial_statements",
            "calculate_financial_metrics",
            "find_similar_events",
        ],
        budget_profile="mvp_standard",
    ),
    AgentRegistration(
        agent_key="skeptic",
        version="1.0.0",
        display_name="反方审查 Agent",
        capabilities=["skeptic_review"],
        input_schema_refs=["company-analysis-output/1.0.0", "fact-check-snapshot/1.0.0"],
        output_schema_ref="skeptic-output/1.0.0",
        allowed_tools=[
            "get_evidence",
            "get_financial_statements",
            "find_similar_events",
        ],
        budget_profile="mvp_standard",
    ),
    AgentRegistration(
        agent_key="synthesizer",
        version="1.0.0",
        display_name="结论合成 Agent",
        capabilities=["synthesize"],
        input_schema_refs=[
            "fact-check-snapshot/1.0.0",
            "company-analysis-output/1.0.0",
            "skeptic-output/1.0.0",
        ],
        output_schema_ref="synthesis-output/1.0.0",
        allowed_tools=["read_blackboard", "resolve_citation"],
        budget_profile="mvp_standard",
    ),
    AgentRegistration(
        agent_key="planner",
        version="1.0.0",
        display_name="研究计划 Agent",
        capabilities=["plan"],
        input_schema_refs=["research-question/1.0.0"],
        output_schema_ref="research-plan/1.0.0",
        allowed_tools=["read_blackboard"],
        budget_profile="mvp_low",
    ),
    AgentRegistration(
        agent_key="retriever",
        version="1.0.0",
        display_name="检索 Agent",
        capabilities=["retrieve"],
        input_schema_refs=["research-question/1.0.0", "retrieval-context/1.0.0"],
        output_schema_ref="retrieval-trace/1.0.0",
        allowed_tools=["planned_retrieval"],
        budget_profile="mvp_standard",
    ),
    AgentRegistration(
        agent_key="impact_analyst",
        version="1.0.0",
        display_name="宏观影响分析 Agent",
        capabilities=["impact_analyze"],
        input_schema_refs=["event-snapshot/1.0.0", "fact-check-snapshot/1.0.0"],
        output_schema_ref="impact-analysis-output/1.0.0",
        allowed_tools=[
            "get_financial_statements",
            "find_similar_events",
        ],
        budget_profile="mvp_standard",
    ),
    AgentRegistration(
        agent_key="market_analyst",
        version="1.0.0",
        display_name="市场情绪与流动性分析 Agent",
        capabilities=["market_analyze"],
        input_schema_refs=["event-snapshot/1.0.0", "fact-check-snapshot/1.0.0"],
        output_schema_ref="market-analysis-output/1.0.0",
        allowed_tools=[
            "get_financial_statements",
            "find_similar_events",
        ],
        budget_profile="mvp_standard",
    ),
    AgentRegistration(
        agent_key="industry_analyst",
        version="1.0.0",
        display_name="产业链与行业分析 Agent",
        capabilities=["industry_analyze"],
        input_schema_refs=["event-snapshot/1.0.0", "fact-check-snapshot/1.0.0"],
        output_schema_ref="industry-analysis-output/1.0.0",
        allowed_tools=[
            "get_financial_statements",
            "find_similar_events",
        ],
        budget_profile="mvp_standard",
    ),
    AgentRegistration(
        agent_key="regulatory_analyst",
        version="1.0.0",
        display_name="政策与监管分析 Agent",
        capabilities=["regulatory_analyze"],
        input_schema_refs=["event-snapshot/1.0.0", "fact-check-snapshot/1.0.0"],
        output_schema_ref="regulatory-analysis-output/1.0.0",
        allowed_tools=[
            "get_financial_statements",
            "find_similar_events",
        ],
        budget_profile="mvp_standard",
    ),
]


class AgentRegistry:
    """Specialist Agent 注册表：支持内存预注册与持久化加载。"""

    def __init__(
        self,
        repository=None,
        registrations: Optional[list[AgentRegistration]] = None,
    ) -> None:
        self.repository = repository
        self._registrations: dict[str, AgentRegistration] = {}
        for registration in registrations or DEFAULT_REGISTRATIONS:
            self.register(registration)
        if repository is not None:
            self._load_from_repository()

    def _load_from_repository(self) -> None:
        """从持久化表加载注册记录；缺失时用默认注册兜底。"""
        try:
            persisted = self.repository.list_agent_registrations()
        except Exception:
            persisted = []
        for registration in persisted:
            self._registrations[registration.agent_key] = registration
        for default in DEFAULT_REGISTRATIONS:
            if default.agent_key not in self._registrations:
                self._registrations[default.agent_key] = default

    def register(self, registration: AgentRegistration) -> AgentRegistration:
        """注册或更新一个 Agent；agent_key 相同则覆盖。"""
        now = datetime.now(timezone.utc)
        if registration.created_at is None:
            registration = replace(registration, created_at=now)
        registration = replace(registration, updated_at=now)
        self._registrations[registration.agent_key] = registration
        if self.repository is not None:
            try:
                self.repository.save_agent_registration(registration)
            except Exception:
                # 持久化失败不阻止内存注册，便于测试
                pass
        return registration

    def get(self, agent_key: str) -> Optional[AgentRegistration]:
        return self._registrations.get(agent_key)

    def list_all(self) -> list[AgentRegistration]:
        return list(self._registrations.values())

    def find(
        self,
        capabilities: Optional[list[str]] = None,
        output_schema_ref: Optional[str] = None,
        input_schema_refs: Optional[list[str]] = None,
    ) -> list[AgentRegistration]:
        """按能力、输出/输入 Schema 匹配候选 Agent；按版本降序排列。"""
        candidates = list(self._registrations.values())
        if capabilities:
            required = set(capabilities)
            candidates = [r for r in candidates if required.issubset(set(r.capabilities))]
        if output_schema_ref:
            candidates = [r for r in candidates if r.output_schema_ref == output_schema_ref]
        if input_schema_refs:
            required_inputs = set(input_schema_refs)
            candidates = [
                r for r in candidates if required_inputs.issubset(set(r.input_schema_refs))
            ]
        return sorted(candidates, key=lambda r: r.version, reverse=True)

    def allowed_tools_for(self, agent_key: str) -> list[str]:
        registration = self.get(agent_key)
        if registration is None:
            return []
        return list(registration.allowed_tools)

    def to_tool_gateway_whitelist(self) -> dict[str, frozenset[str]]:
        """将注册表转换为 ToolGateway 可用的白名单格式。"""
        return {
            r.agent_key: frozenset(r.allowed_tools)
            for r in self._registrations.values()
        }
