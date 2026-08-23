"""Classify model-call failures without swallowing the root cause.

Agent nodes keep their degraded fallbacks, but operators need the exception
type and a coarse reason (timeout / quota / schema / invoke) in logs and
audits.  Classification is string-based so provider SDKs stay optional.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelCallFailure:
    code: str
    stage: str
    exception_type: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "stage": self.stage,
            "exception_type": self.exception_type,
            "message": self.message[:500],
        }


def classify_model_failure(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    text = f"{name} {exc}".lower()
    if "timeout" in text or name.endswith("timeouterror"):
        return "timeout"
    if any(
        token in text
        for token in ("quota", "rate_limit", "ratelimit", "429", "too many requests")
    ):
        return "quota"
    if any(
        token in text
        for token in (
            "validationerror",
            "schema",
            "extra_forbidden",
            "jsondecode",
            "json_invalid",
        )
    ):
        return "schema_invalid"
    return "invoke_error"


def record_model_failure(
    logger: logging.Logger,
    *,
    operation: str,
    stage: str,
    exc: BaseException,
) -> ModelCallFailure:
    failure = ModelCallFailure(
        code=classify_model_failure(exc),
        stage=stage,
        exception_type=type(exc).__name__,
        message=str(exc) or type(exc).__name__,
    )
    logger.warning(
        "model call degraded: operation=%s stage=%s code=%s type=%s error=%s",
        operation,
        stage,
        failure.code,
        failure.exception_type,
        failure.message,
        exc_info=True,
    )
    return failure
