import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api.schemas import ApiErrorBody, ApiErrorMeta

logger = logging.getLogger(__name__)

# Shared OpenAPI response examples (DD-00). Status keys are illustrative; handlers always
# emit ErrorEnvelope regardless of route-level annotations.
OPENAPI_ERROR_EXAMPLES: dict[str, dict[str, Any]] = {
    "400": {
        "summary": "Bad request (stable code)",
        "value": {
            "error": {
                "code": "INVALID_CURSOR",
                "message": "Invalid Cursor",
                "retryable": False,
                "details": {},
            },
            "meta": {"request_id": "req_019example"},
        },
    },
    "401": {
        "summary": "Unauthorized",
        "value": {
            "error": {
                "code": "AUTH_INVALID_CREDENTIALS",
                "message": "Auth Invalid Credentials",
                "retryable": False,
                "details": {},
            },
            "meta": {"request_id": "req_019example"},
        },
    },
    "404": {
        "summary": "Not found",
        "value": {
            "error": {
                "code": "SOURCE_NOT_FOUND",
                "message": "Source Not Found",
                "retryable": False,
                "details": {},
            },
            "meta": {"request_id": "req_019example"},
        },
    },
    "409": {
        "summary": "Conflict",
        "value": {
            "error": {
                "code": "IDEMPOTENCY_CONFLICT",
                "message": "Idempotency Conflict",
                "retryable": False,
                "details": {},
            },
            "meta": {"request_id": "req_019example"},
        },
    },
    "422": {
        "summary": "Validation failed",
        "value": {
            "error": {
                "code": "REQUEST_VALIDATION_FAILED",
                "message": "Request validation failed",
                "retryable": False,
                "details": [
                    {
                        "location": ["query", "limit"],
                        "message": "Input should be less than or equal to 100",
                        "type": "less_than_equal",
                    }
                ],
            },
            "meta": {"request_id": "req_019example"},
        },
    },
    "500": {
        "summary": "Internal error",
        "value": {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Internal server error",
                "retryable": False,
                "details": {},
            },
            "meta": {"request_id": "req_019example"},
        },
    },
}


def openapi_error_responses(*statuses: int) -> dict[int, dict[str, str]]:
    """Route-level OpenAPI ``responses`` entries referencing shared Error* components."""

    return {
        status: {"$ref": f"#/components/responses/Error{status}"} for status in statuses
    }


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    details: object = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            },
            "meta": {"request_id": request.state.request_id},
        },
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "location": list(error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        return error_response(
            request,
            status_code=422,
            code="REQUEST_VALIDATION_FAILED",
            message="Request validation failed",
            details=details,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = str(exc.detail) if isinstance(exc.detail, str) else "HTTP_ERROR"
        return error_response(
            request,
            status_code=exc.status_code,
            code=code,
            message=code.replace("_", " ").title(),
            details={} if isinstance(exc.detail, str) else exc.detail,
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API error", exc_info=exc)
        return error_response(
            request,
            status_code=500,
            code="INTERNAL_ERROR",
            message="Internal server error",
            retryable=False,
        )


def install_openapi_error_examples(app: FastAPI) -> None:
    """Register DD-00 ErrorEnvelope schema and shared 4xx/5xx response examples."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        schemas = components.setdefault("schemas", {})
        schemas["ApiErrorBody"] = ApiErrorBody.model_json_schema()
        schemas["ApiErrorMeta"] = ApiErrorMeta.model_json_schema()
        schemas["ErrorEnvelope"] = {
            "title": "ErrorEnvelope",
            "type": "object",
            "required": ["error", "meta"],
            "properties": {
                "error": {"$ref": "#/components/schemas/ApiErrorBody"},
                "meta": {"$ref": "#/components/schemas/ApiErrorMeta"},
            },
            "description": "DD-00 error response envelope for all 4xx/5xx responses.",
        }

        responses = components.setdefault("responses", {})
        for status, example in OPENAPI_ERROR_EXAMPLES.items():
            responses[f"Error{status}"] = {
                "description": f"DD-00 error envelope ({status})",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ErrorEnvelope"},
                        "examples": {f"error_{status}": example},
                    }
                },
            }
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
