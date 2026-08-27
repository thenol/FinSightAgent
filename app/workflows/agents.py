"""MVP 研究 Agent。

每个 Agent 组装 ModelRequest、调用 ModelGateway、用 Pydantic 模型校验输出后写入
Blackboard（DD-50 §11）。MVP 阶段输出由确定性逻辑基于 Blackboard 事实构造，
保证可重放与可测试；接入真实供应商后只替换 ModelProvider，Agent 编排不变。

约束（DD-50 §11、§12）：
- 输出区分事实（claim_ids）、假设（assumptions）、推论（scenarios）。
- 数值计算走 ToolGateway 工具，不依赖模型心算。
- Synthesizer 禁止搜索与行情查询，只读已有结构化结果。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from app.model_gateway.service import ModelGateway, ModelRequest
from app.platform.asof import ensure_within_as_of
from app.research.tools.gateway import ToolGateway
from app.workflows.schemas import (
    CompanyAnalysisOutput,
    FinancialImpact,
    SkepticOutput,
    SynthesisOutput,
)

AGENT_SCHEMA_VERSION = "v1"


def _try_parse_payload(payload: Any, schema: type[BaseModel]) -> BaseModel | None:
    if not payload or not isinstance(payload, dict):
        return None
    try:
        return schema.model_validate(payload)
    except ValidationError:
        return None


def _parse_as_of(state: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(state["as_of"])


class FactCheckerAgent:
    """事实核验：只产出事实、冲突与未验证声明。"""

    agent_type = "fact_checker"
    operation = "fact_check"

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def run(self, state: dict[str, Any], repository, tool_gateway: ToolGateway) -> dict[str, Any]:
        as_of = _parse_as_of(state)
        event = repository.get_event(state["event_id"])
        ensure_within_as_of(event, as_of, context="fact_check:get_event")
        claims = repository.get_claims_for_event(state["event_id"], as_of=as_of)
        verified = [claim for claim in claims if claim.status == "verified"]
        response = self.gateway.invoke(
            ModelRequest(
                operation=self.operation,
                input_schema_version=AGENT_SCHEMA_VERSION,
                output_schema_version=AGENT_SCHEMA_VERSION,
                payload={
                    "event_id": state["event_id"],
                    "claim_ids": [claim.id for claim in claims],
                    "verified_count": len(verified),
                },
            )
        )
        return {
            "fact_check_snapshot": {
                "event_id": state["event_id"],
                "model_run_id": response.run_id,
                "analysis_ref": "fact_check_snapshot",
                "claim_ids": [claim.id for claim in claims],
                "verified_claim_ids": [claim.id for claim in verified],
            }
        }


class CompanyAnalystAgent:
    """公司基本面分析：区分一次性与持续影响，给出情景与假设。"""

    agent_type = "company_analyst"
    operation = "company_analysis"

    def __init__(self, gateway: ModelGateway, tool_gateway: ToolGateway) -> None:
        self.gateway = gateway
        self.tool_gateway = tool_gateway

    def run(self, state: dict[str, Any], repository) -> dict[str, Any]:
        as_of = _parse_as_of(state)
        snapshot = state.get("fact_check_snapshot", {})
        claim_ids = snapshot.get("verified_claim_ids", [])
        event = repository.get_event(state["event_id"])

        # 数值计算走工具，避免模型心算（DD §4.3）；调用经 ToolGateway 鉴权与审计。
        tool_result = self.tool_gateway.invoke(
            workflow_id=state.get("workflow_id", ""),
            agent_type=self.agent_type,
            tool_name="calculate_financial_metrics",
            arguments={"expression": "change_rate,net_profit"},
            as_of=as_of,
        )
        tool_result_ids = [f"calc-{len(tool_result.get('items', []))}"]

        response = self.gateway.invoke(
            ModelRequest(
                operation=self.operation,
                input_schema_version=AGENT_SCHEMA_VERSION,
                output_schema_version=AGENT_SCHEMA_VERSION,
                payload={
                    "event_id": state["event_id"],
                    "event_type": event.event_type if event else "unknown",
                    "verified_claim_ids": claim_ids,
                    "tool_result_ids": tool_result_ids,
                    "preliminary_assessment": state.get("preliminary_assessment", {}),
                },
            )
        )

        direction = self._direction(event)
        parsed = _try_parse_payload(response.payload, CompanyAnalysisOutput)
        if parsed is not None:
            if not parsed.financial_impacts:
                parsed = parsed.model_copy(
                    update={
                        "financial_impacts": [
                            FinancialImpact(
                                metric="net_profit",
                                direction=("increase" if direction == "positive" else "uncertain"),
                                period=(
                                    event.key_fields.get("period", "unknown")
                                    if event
                                    else "unknown"
                                ),
                                basis="公告披露",
                                estimated_change=(
                                    event.key_fields.get("change_rate") if event else None
                                ),
                                claim_ids=claim_ids,
                                tool_result_ids=tool_result_ids,
                            )
                        ]
                    }
                )
            output = parsed.model_copy(update={"model_run_id": response.run_id})
        else:
            output = CompanyAnalysisOutput(
                model_run_id=response.run_id,
                status="complete" if claim_ids else "partial",
                direction=direction,
                impact_horizon="short_term",
                assumptions=[
                    {
                        "assumption_id": "asm_001",
                        "statement": "公告披露的业绩区间可信",
                        "importance": "high",
                        "supporting_claim_ids": claim_ids[:1],
                    }
                ],
                financial_impacts=[
                    {
                        "metric": "net_profit",
                        "direction": "increase" if direction == "positive" else "uncertain",
                        "period": event.key_fields.get("period", "unknown") if event else "unknown",
                        "basis": "公告业绩预告",
                        "estimated_change": event.key_fields.get("change_rate") if event else None,
                        "claim_ids": claim_ids,
                        "tool_result_ids": tool_result_ids,
                    }
                ],
                scenarios=[
                    {
                        "name": "base",
                        "assumption_ids": ["asm_001"],
                        "outcome": "增长部分持续",
                        "probability_label": "medium",
                    }
                ],
                risks=[
                    {
                        "risk_id": "risk_001",
                        "statement": "市场可能已提前定价",
                        "severity": "medium",
                        "monitoring_indicator": "公告后5日相对收益",
                    }
                ],
                confidence=0.65 if claim_ids else 0.40,
                confidence_factors=["verified_claim_count", "source_tier"],
            )
        return {"company_analysis": output.model_dump()}

    def _direction(self, event) -> str:
        if event is None:
            return "uncertain"
        if event.event_type in {"earnings_guidance", "major_contract"}:
            return "positive"
        if event.event_type in {"shareholder_reduction", "regulatory_penalty"}:
            return "negative"
        return "neutral"


class SkepticAgent:
    """反方审查：提出反证、脆弱假设与结论反转条件。"""

    agent_type = "skeptic"
    operation = "skeptic_review"

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def run(self, state: dict[str, Any], repository) -> dict[str, Any]:
        company = state.get("company_analysis", {})
        claim_ids = (
            company.get("financial_impacts", [{}])[0].get("claim_ids", []) if company else []
        )
        response = self.gateway.invoke(
            ModelRequest(
                operation=self.operation,
                input_schema_version=AGENT_SCHEMA_VERSION,
                output_schema_version=AGENT_SCHEMA_VERSION,
                payload={
                    "company_analysis_summary": company.get("financial_impacts"),
                    "claim_ids": claim_ids,
                    "preliminary_assessment": state.get("preliminary_assessment", {}),
                },
            )
        )
        parsed = _try_parse_payload(response.payload, SkepticOutput)
        if parsed is not None:
            output = parsed.model_copy(update={"model_run_id": response.run_id})
        else:
            output = SkepticOutput(
                model_run_id=response.run_id,
                status="complete" if claim_ids else "insufficient_evidence",
                counter_arguments=[
                    {
                        "argument_id": "ctr_001",
                        "statement": "股价可能已在公告前反映利好",
                        "severity": "medium",
                        "claim_ids": claim_ids[:1],
                        "targets": ["asm_001"],
                    }
                ],
                fragile_assumptions=[
                    {
                        "assumption_id": "asm_001",
                        "failure_mode": "增长不持续",
                        "materiality": "high",
                    }
                ],
                thesis_breakers=[
                    {
                        "condition": "下一季度毛利率低于阈值",
                        "indicator": "主营业务毛利率",
                        "threshold": "18%",
                        "horizon": "1 quarter",
                    }
                ],
                direction_assessment="weakens",
                recommended_confidence=0.55,
                confidence_reasons=["公告前涨幅未纳入", "一次性收益占比待核"],
                review_required=False,
            )
        return {"counter_analysis": output.model_dump()}


class SynthesizerAgent:
    """结论合成：只基于已有结构化结果，禁止搜索与行情查询。"""

    agent_type = "synthesizer"
    operation = "synthesize"

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def run(self, state: dict[str, Any], repository) -> dict[str, Any]:
        snapshot = state.get("fact_check_snapshot", {})
        company = state.get("company_analysis", {})
        counter = state.get("counter_analysis", {})
        key_facts = snapshot.get("verified_claim_ids", [])
        response = self.gateway.invoke(
            ModelRequest(
                operation=self.operation,
                input_schema_version=AGENT_SCHEMA_VERSION,
                output_schema_version=AGENT_SCHEMA_VERSION,
                payload={
                    "key_fact_claim_ids": key_facts,
                    "company_direction": company.get("direction"),
                    "counter_direction": counter.get("direction_assessment"),
                    "preliminary_assessment": state.get("preliminary_assessment", {}),
                    "preliminary_assessment_ref": state.get("preliminary_assessment_ref"),
                },
            )
        )
        parsed = _try_parse_payload(response.payload, SynthesisOutput)
        if parsed is not None:
            output = parsed.model_copy(update={"model_run_id": response.run_id})
        else:
            signal = (
                "moderately_positive" if company.get("direction") == "positive" else "uncertain"
            )
            output = SynthesisOutput(
                model_run_id=response.run_id,
                status="complete" if key_facts else "fact_only",
                signal=signal,
                confidence=min(
                    company.get("confidence", 0.4), counter.get("recommended_confidence", 0.5)
                ),
                horizon=company.get("impact_horizon", "uncertain"),
                summary=f"基于 {len(key_facts)} 条已验证事实合成结论。",
                key_fact_claim_ids=key_facts,
                supporting_points=[
                    {
                        "statement": company.get("financial_impacts", [{}])[0].get(
                            "basis", "公告披露"
                        ),
                        "claim_ids": key_facts[:1],
                        "analysis_refs": ["company_analysis"],
                    }
                ]
                if key_facts
                else [],
                counter_points=[
                    {
                        "statement": counter.get("counter_arguments", [{}])[0].get(
                            "statement", "无反证"
                        ),
                        "counter_argument_ids": ["ctr_001"],
                    }
                ]
                if counter
                else [],
                watch_items=[
                    {
                        "indicator": "主营业务毛利率",
                        "reason": "判断增长持续性",
                        "horizon": "1 quarter",
                    }
                ],
                reanalysis_triggers=[
                    {
                        "trigger_type": "new_filing",
                        "condition": "公司发布业绩修正公告",
                        "affected_nodes": ["fact_check", "company_analysis", "synthesize"],
                    }
                ],
                limitations=["MVP 阶段未接入市场预期与定价分析"],
                confidence_factors=["verified_claims", "skeptic_adjustment"],
                preliminary_assessment_id=state.get("preliminary_assessment_ref"),
                assessment_disposition=(
                    "revised" if state.get("preliminary_assessment") else "insufficient"
                ),
                assessment_delta={"direction": "待正式结论验证"}
                if state.get("preliminary_assessment")
                else {},
                delta_reasons=["正式结论尚未完成独立反方审查"]
                if state.get("preliminary_assessment")
                else [],
            )
        return {"synthesis": output.model_dump()}
