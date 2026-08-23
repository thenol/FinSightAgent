from pydantic import ValidationError

from app.model_gateway.failures import classify_model_failure


def test_classify_model_failure_timeout_quota_and_schema() -> None:
    assert classify_model_failure(TimeoutError("gateway timeout")) == "timeout"
    assert classify_model_failure(RuntimeError("HTTP 429 too many requests")) == "quota"
    try:
        from pydantic import BaseModel

        class Payload(BaseModel):
            name: str

        Payload.model_validate({})
    except ValidationError as exc:
        assert classify_model_failure(exc) == "schema_invalid"
    assert classify_model_failure(RuntimeError("connection reset")) == "invoke_error"
