from pydantic import BaseModel, Field


class AutoReviewDecision(BaseModel):
    """Default Reviewer Agent 的输出。"""

    decision: str = Field(
        default="",
        description="建议决定，如 approve / reject / return / merge / new_event / skip",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")
    escalate: bool = Field(default=True)
    context: dict = Field(default_factory=dict)
    model_run_id: str | None = None


class DefaultReviewerOutput(BaseModel):
    """LLM 返回的结构化结果。"""

    decision: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")
    escalate: bool = Field(default=True)
