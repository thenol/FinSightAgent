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
