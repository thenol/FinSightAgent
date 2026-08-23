import logging
from typing import Any

from app.model_gateway.failures import record_model_failure
from app.model_gateway.service import ModelGateway, ModelRequest
from app.review.schemas import AutoReviewDecision, DefaultReviewerOutput

logger = logging.getLogger(__name__)

AGENT_SCHEMA_VERSION = "v1"


class DefaultReviewerAgent:
    """默认审核 Agent：基于任务上下文给出自动审核决定或建议升级人工。"""

    agent_type = "default_reviewer"
    operation = "default_reviewer"

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def decide(self, context: dict[str, Any]) -> AutoReviewDecision:
        """调用模型；若未配置 provider 或输出无法解析，则返回 escalate。"""
        try:
            response = self.gateway.invoke(
                ModelRequest(
                    operation=self.operation,
                    input_schema_version=AGENT_SCHEMA_VERSION,
                    output_schema_version=AGENT_SCHEMA_VERSION,
                    payload=context,
                    timeout_seconds=30,
                )
            )
        except Exception as exc:
            failure = record_model_failure(
                logger, operation=self.operation, stage="invoke", exc=exc
            )
            return AutoReviewDecision(
                escalate=True,
                reason=(
                    f"default_reviewer 调用失败"
                    f"({failure.code}/{failure.exception_type})，转人工"
                ),
            )

        payload = response.payload if isinstance(response.payload, dict) else {}
        try:
            parsed = DefaultReviewerOutput.model_validate(payload)
        except Exception as exc:
            failure = record_model_failure(
                logger, operation=self.operation, stage="schema", exc=exc
            )
            return AutoReviewDecision(
                escalate=True,
                reason=(
                    f"default_reviewer 输出无法解析"
                    f"({failure.code}/{failure.exception_type})，转人工"
                ),
            )

        return AutoReviewDecision(
            decision=parsed.decision,
            confidence=parsed.confidence,
            reason=parsed.reason,
            escalate=parsed.escalate,
        )
