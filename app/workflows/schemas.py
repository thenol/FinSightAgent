"""Agent 输出 Pydantic 模型。

对应 ``docs/design/schemas/`` 下的 JSON Schema，用于校验 Agent 输出。Agent 输出
必须通过 Schema 校验后才进入 Blackboard（DD-50 §11）。

每个模型区分事实、假设与推论：
- CompanyAnalysis：financial_impacts 携带 claim_ids（事实）与 tool_result_ids（计算），
  assumptions 是假设，scenarios 是推论。
- Skeptic：counter_arguments 引用 claim_ids（反证事实），thesis_breakers 是推论。
- Synthesis：key_fact_claim_ids 引用事实，supporting_points 引用 analysis_refs（推论依据）。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CompanyAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assumption_id: str = Field(pattern=r"^asm_[a-zA-Z0-9_-]+$")
    statement: str = Field(min_length=1)
    importance: Literal["low", "medium", "high", "critical"]
    supporting_claim_ids: list[str] = Field(default_factory=list)


class FinancialImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Literal[
        "revenue", "gross_margin", "operating_profit", "net_profit", "eps", "cash_flow", "other"
    ]
    direction: Literal["increase", "decrease", "unchanged", "uncertain"]
    period: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    estimated_change: dict | None = None
    claim_ids: list[str] = Field(default_factory=list)
    tool_result_ids: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["bear", "base", "bull"]
    assumption_ids: list[str] = Field(default_factory=list)
    outcome: str = Field(min_length=1)
    probability_label: Literal["low", "medium", "high", "not_assessed"]


class Risk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk_id: str = Field(pattern=r"^risk_[a-zA-Z0-9_-]+$")
    statement: str = Field(min_length=1)
    severity: Literal["low", "medium", "high", "critical"]
    monitoring_indicator: str = Field(min_length=1)


class CompanyAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    model_run_id: str | None = None
    analysis_ref: Literal["company_analysis"] = "company_analysis"
    status: Literal["complete", "partial", "insufficient_data"]
    direction: Literal["positive", "negative", "mixed", "neutral", "uncertain"]
    impact_horizon: Literal["immediate", "short_term", "medium_term", "long_term", "uncertain"]
    assumptions: list[CompanyAssumption] = Field(default_factory=list)
    financial_impacts: list[FinancialImpact] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(min_length=1)
    risks: list[Risk] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    confidence_factors: list[str] = Field(default_factory=list)


class CounterArgument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    argument_id: str = Field(pattern=r"^ctr_[a-zA-Z0-9_-]+$")
    statement: str = Field(min_length=1)
    severity: Literal["low", "medium", "high", "critical"]
    claim_ids: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)


class FragileAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assumption_id: str
    failure_mode: str = Field(min_length=1)
    materiality: Literal["low", "medium", "high", "critical"]


class ThesisBreaker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition: str = Field(min_length=1)
    indicator: str = Field(min_length=1)
    threshold: str = Field(min_length=1)
    horizon: str = Field(min_length=1)


class SkepticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    model_run_id: str | None = None
    analysis_ref: Literal["counter_analysis"] = "counter_analysis"
    status: Literal["complete", "partial", "insufficient_evidence"]
    counter_arguments: list[CounterArgument] = Field(default_factory=list)
    fragile_assumptions: list[FragileAssumption] = Field(default_factory=list)
    thesis_breakers: list[ThesisBreaker] = Field(default_factory=list)
    direction_assessment: Literal["supports", "weakens", "reverses", "mixed", "inconclusive"]
    recommended_confidence: float = Field(ge=0, le=1)
    confidence_reasons: list[str] = Field(default_factory=list)
    review_required: bool
    review_reason: str | None = None


class SupportingPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    analysis_refs: list[str] = Field(default_factory=list)


class CounterPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1)
    counter_argument_ids: list[str] = Field(default_factory=list)


class WatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indicator: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    horizon: str = Field(min_length=1)


class ReanalysisTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger_type: Literal[
        "new_filing",
        "claim_change",
        "financial_metric",
        "price_move",
        "regulatory_action",
        "manual",
    ]
    condition: str = Field(min_length=1)
    affected_nodes: list[
        Literal[
            "fact_check", "company_analysis", "skeptic_review", "synthesize", "validate_guardrails"
        ]
    ] = Field(default_factory=list)


class SynthesisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    model_run_id: str | None = None
    analysis_ref: Literal["synthesis"] = "synthesis"
    status: Literal["complete", "partial", "fact_only", "needs_review"]
    signal: Literal[
        "strongly_positive",
        "moderately_positive",
        "neutral",
        "mixed",
        "moderately_negative",
        "strongly_negative",
        "uncertain",
    ]
    confidence: float = Field(ge=0, le=1)
    horizon: Literal["immediate", "short_term", "medium_term", "long_term", "uncertain"]
    summary: str = Field(min_length=1)
    key_fact_claim_ids: list[str] = Field(default_factory=list)
    supporting_points: list[SupportingPoint] = Field(default_factory=list)
    counter_points: list[CounterPoint] = Field(default_factory=list)
    watch_items: list[WatchItem] = Field(default_factory=list)
    reanalysis_triggers: list[ReanalysisTrigger] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence_factors: list[str] = Field(default_factory=list)
    preliminary_assessment_id: str | None = None
    assessment_disposition: Literal["upheld", "revised", "overturned", "insufficient"] = (
        "insufficient"
    )
    assessment_delta: dict = Field(default_factory=dict)
    delta_reasons: list[str] = Field(default_factory=list)


class ResearchMemoSection(BaseModel):
    """A non-overlapping paragraph in the reader-facing research memo."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["why_now", "mechanism", "evidence", "counter_case", "watch"]
    title: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=3000)
    claim_ids: list[str] = Field(default_factory=list)
    card_refs: list[str] = Field(default_factory=list)


class ResearchMemoOutput(BaseModel):
    """Constrained prose built from verified facts and analysis cards only."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0.0"] = "2.0.0"
    model_run_id: str | None = None
    analysis_ref: Literal["research_memo"] = "research_memo"
    status: Literal["complete", "evidence_limited"]
    conclusion: str = Field(min_length=1, max_length=1200)
    direction: Literal[
        "strongly_positive",
        "moderately_positive",
        "neutral",
        "mixed",
        "moderately_negative",
        "strongly_negative",
        "uncertain",
    ]
    horizon: Literal["immediate", "short_term", "medium_term", "long_term", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    sections: list[ResearchMemoSection] = Field(min_length=1, max_length=5)
