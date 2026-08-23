"""影响分析 Agent 的输入/输出 Schema。

对应 ``docs/design/schemas/impact-analysis-output.schema.json``。输出经 Pydantic
校验后持久化为 ``ImpactAnalysis``；解析失败时服务层降级为规则模板。
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class TransmissionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: int = Field(ge=0)
    description: str = Field(min_length=1)


class TransmissionChain(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_id: str = Field(pattern=r"^chn_[a-zA-Z0-9_-]+$")
    mechanism: str = Field(min_length=1)
    steps: list[TransmissionStep] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ImpactTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: Literal["sector", "industry", "company", "macro_variable", "market", "asset_class"]
    target_name: str = Field(min_length=1)
    # 标准化标识符，可选（如 A 股代码、板块代码、宏观指标代码）
    target_code: str | None = None
    direction: Literal["positive", "negative", "neutral", "mixed"]
    magnitude: Literal["strong", "moderate", "weak", "uncertain"]
    horizon: Literal["short", "medium", "long", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)
    # 引用本输出中的传导链 id
    chain_refs: list[str] = Field(default_factory=list)
    # 相关已验证 claim id，可选
    claim_ids: list[str] = Field(default_factory=list)


class ImpactAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    summary: str = Field(min_length=1)
    transmission_chains: list[TransmissionChain] = Field(default_factory=list)
    impacts: list[ImpactTarget] = Field(min_length=1)
    macro_assumptions: list[str] = Field(default_factory=list)
    watch_items: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    # 生成该输出的模型运行 id，用于成本与审计追踪
    model_run_id: Optional[str] = None


# V2：因果图是权威产物，旧版字段仅作为兼容投影保留。
class EvidenceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_type: Literal["claim", "document", "metric", "analogue", "assumption"]
    evidence_id: str = Field(min_length=1)
    stance: Literal["supports", "contradicts", "context"] = "supports"
    as_of: Optional[str] = None


class CausalNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(pattern=r"^node_[a-zA-Z0-9_-]+$")
    node_type: Literal["event", "mechanism", "variable", "entity", "impact"]
    label: str = Field(min_length=1)
    entity_id: Optional[str] = None
    layer: int = Field(default=0, ge=0, le=8)
    group: Optional[str] = None


class CausalEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edge_id: str = Field(pattern=r"^edge_[a-zA-Z0-9_-]+$")
    source_node_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    direction: Literal["positive", "negative", "mixed", "uncertain"]
    order: Literal["direct", "first_order", "second_order"] = "direct"
    horizon: Literal["0_1d", "2_5d", "1_4w", "1_4q", "1y_plus", "unknown"] = "unknown"
    conditions: list[str] = Field(default_factory=list)
    invalidators: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceBinding] = Field(default_factory=list)
    inference_kind: Literal["fact", "derived", "analogue", "inference", "assumption"]
    confidence: float = Field(ge=0, le=1)


class CausalGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[CausalNode] = Field(min_length=1)
    edges: list[CausalEdge] = Field(default_factory=list)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(pattern=r"^scn_[a-zA-Z0-9_-]+$")
    name: Literal["base", "upside", "downside", "alternative"]
    assumptions: list[str] = Field(min_length=1)
    active_edge_ids: list[str] = Field(default_factory=list)
    invalidators: list[str] = Field(default_factory=list)
    likelihood: Literal["high", "medium", "low", "unknown"] = "unknown"


class ImpactDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: Literal[
        "demand", "revenue", "cost", "margin", "cash_flow", "valuation",
        "liquidity", "credit", "supply", "fx", "commodity", "other",
    ]
    direction: Literal["positive", "negative", "mixed", "uncertain"]
    magnitude: Literal["strong", "moderate", "weak", "uncertain"]
    quantitative_range: Optional[str] = None


class ImpactTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    onset_at: Optional[str] = None
    expected_peak_at: Optional[str] = None
    valid_to: Optional[str] = None
    basis: Literal["evidence", "inferred", "assumption", "unknown"] = "unknown"
    confidence: float = Field(default=0.0, ge=0, le=1)


class ImpactAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_id: str = Field(pattern=r"^ia_[a-zA-Z0-9_-]+$")
    scenario_id: str = Field(min_length=1)
    target_type: Literal["sector", "industry", "company", "macro_variable", "market", "asset_class"]
    target_name: str = Field(min_length=1)
    target_code: Optional[str] = None
    exposure_path: list[str] = Field(min_length=1)
    dimensions: list[ImpactDimension] = Field(min_length=1)
    horizon: Literal["0_1d", "2_5d", "1_4w", "1_4q", "1y_plus", "unknown"]
    evidence_refs: list[EvidenceBinding] = Field(default_factory=list)
    causal_edge_refs: list[str] = Field(default_factory=list)
    timing: ImpactTiming = Field(default_factory=ImpactTiming)
    confidence: float = Field(ge=0, le=1)


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_coverage: float = Field(ge=0, le=1)
    unresolved_references: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    gate_passed: bool = False


class ImpactAnalysisOutputV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0.0", "2.1.0"] = "2.0.0"
    summary: str = Field(min_length=1)
    context_snapshot: dict = Field(default_factory=dict)
    causal_graph: CausalGraph
    scenarios: list[Scenario] = Field(min_length=1)
    impact_assessments: list[ImpactAssessment] = Field(min_length=1)
    watch_items: list[str] = Field(default_factory=list)
    quality_report: QualityReport
    model_run_id: Optional[str] = None
