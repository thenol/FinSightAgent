from __future__ import annotations

import json
import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.admin_ui import router as admin_router
from app.api.auth import PASSWORD_HASH, TokenManager
from app.api.errors import install_exception_handlers, install_openapi_error_examples
from app.api.routes import router
from app.application.pipeline import EventResearchPipeline
from app.domain import User
from app.ingestion.artifacts import LocalArtifactStore
from app.ingestion.rss import RssFeedClient
from app.ingestion.sync import RssSyncService
from app.market.adapters import (
    AkShareMarketDataProvider,
    EastMoneyBridgeMarketDataProvider,
    EastMoneyMarketDataProvider,
    FallbackMarketDataProvider,
)
from app.market.calendar import build_trading_calendar
from app.market.master_data import seed_market_master_data
from app.market.provider import UnavailableMarketDataProvider
from app.platform.ids import new_id
from app.platform.observability import Observability, TraceContext
from app.platform.repository import InMemoryRepository, SqlAlchemyRepository
from app.platform.settings import Settings

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SENSITIVE_LOG_VALUE = re.compile(
    r"""(?ix)\b("?(
        authorization|password|passwd|secret|token|api[_-]?key
    )"?)(\s*[=:]\s*|\s+)("[^"]*"|'[^']*'|[^\s,;}]+)"""
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SK_LIKE = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,}|sk-ant-[A-Za-z0-9_\-]{8,})\b")


def _redact_log_message(message: str) -> str:
    message = _BEARER_TOKEN.sub("Bearer <redacted>", message)
    message = _SENSITIVE_LOG_VALUE.sub(r"\1\3<redacted>", message)
    return _SK_LIKE.sub("<redacted-key>", message)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_log_message(record.getMessage()),
        }
        for field in (
            "request_id",
            "method",
            "route",
            "status_code",
            "duration_ms",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    if not any(isinstance(item.formatter, JsonFormatter) for item in root.handlers):
        root.handlers[:] = [handler]
        root.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_environment()
    configure_logging()
    if settings.repository == "postgresql":
        repository = SqlAlchemyRepository(settings.database_url)
    elif settings.repository == "memory":
        from pathlib import Path

        override = getattr(app.state, "llm_config_path_override", None)
        if override:
            llm_config_path = str(override)
        else:
            llm_config_path = str(Path(settings.artifact_root).resolve().parent / "llm_config.json")
        repository = InMemoryRepository(llm_config_path=llm_config_path)
    else:
        raise RuntimeError(f"Unsupported repository: {settings.repository}")
    app.state.repository = repository
    app.state.settings = settings
    app.state.market_instruments = seed_market_master_data(repository)
    app.state.market_calendar = build_trading_calendar()
    eastmoney_provider = EastMoneyMarketDataProvider(
        app.state.market_instruments.as_mapping(),
        timeout_seconds=settings.market_data_timeout_seconds,
    )
    akshare_provider = AkShareMarketDataProvider()
    bridge_provider = EastMoneyBridgeMarketDataProvider(
        app.state.market_instruments.as_mapping(),
        base_url=settings.market_data_bridge_url,
        timeout_seconds=settings.market_data_timeout_seconds,
    )
    if settings.market_data_provider == "eastmoney":
        app.state.market_data_provider = eastmoney_provider
    elif settings.market_data_provider == "bridge":
        # Prefer the local browser bridge (session-aware and replayable), but
        # route incomplete history through direct EastMoney and then AKShare.
        app.state.market_data_provider = FallbackMarketDataProvider(
            bridge_provider,
            FallbackMarketDataProvider(eastmoney_provider, akshare_provider),
        )
    elif settings.market_data_provider == "akshare":
        app.state.market_data_provider = akshare_provider
    elif settings.market_data_provider == "none":
        app.state.market_data_provider = UnavailableMarketDataProvider()
    else:
        app.state.market_data_provider = FallbackMarketDataProvider(
            eastmoney_provider, akshare_provider
        )
    app.state.token_manager = TokenManager(settings.jwt_secret)
    bootstrap_username = settings.bootstrap_admin_username
    bootstrap_password = settings.bootstrap_admin_password
    if bootstrap_username and bootstrap_password:
        if not repository.get_user_by_username(bootstrap_username):
            repository.save_user(
                User(
                    id=new_id("usr"),
                    username=bootstrap_username,
                    password_hash=PASSWORD_HASH.hash(bootstrap_password),
                    role="admin",
                )
            )
    elif settings.environment == "development" and not repository.get_user_by_username("admin"):
        repository.save_user(
            User(
                id=new_id("usr"),
                username="admin",
                password_hash=PASSWORD_HASH.hash("admin123"),
                role="admin",
            )
        )
    app.state.pipeline = EventResearchPipeline(
        repository,
        LocalArtifactStore(settings.artifact_root),
        settings=settings,
    )
    app.state.rss_sync = RssSyncService(
        repository,
        app.state.pipeline,
        client=RssFeedClient(timeout_seconds=settings.fetch_timeout_seconds),
        settings=settings,
    )
    try:
        yield
    finally:
        await app.state.rss_sync.client.close()


def create_app(
    observability: Observability | None = None,
    *,
    llm_config_path: str | None = None,
) -> FastAPI:
    application = FastAPI(
        title="FinSightAgent API",
        version="0.1.0",
        description="Evidence-first financial event research API",
        lifespan=lifespan,
    )
    application.state.observability = observability or Observability.no_op()
    application.state.llm_config_path_override = llm_config_path

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else new_id("req")
        )
        parent_context = TraceContext.from_traceparent(request.headers.get("traceparent"))
        telemetry = request.app.state.observability
        started = time.perf_counter()
        status_code = 500
        route = "unmatched"
        with telemetry.stage(
            "http",
            "request",
            parent=parent_context,
            attributes={"http.method": request.method},
        ) as span:
            request.state.trace_context = span.context
            try:
                response = await call_next(request)
                status_code = response.status_code
            finally:
                route_object = request.scope.get("route")
                route = getattr(route_object, "path", "unmatched")
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                span.set_attribute("http.route", route)
                span.set_attribute("http.status_code", status_code)
                if status_code >= 500:
                    span.set_status("error")
                labels = {
                    "stage": "http",
                    "operation": "request",
                    "method": request.method,
                    "route": route,
                    "status_code": str(status_code),
                    "status_class": f"{status_code // 100}xx",
                }
                telemetry.latency(duration_ms, **labels)
                telemetry.success(status_code < 500, **labels)
                logging.getLogger("finsight.request").info(
                    "HTTP request completed",
                    extra={
                        "request_id": request.state.request_id,
                        "method": request.method,
                        "route": route,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    },
                )
            response.headers["X-Request-ID"] = request.state.request_id
            response.headers["traceparent"] = span.context.as_traceparent()
            return response

    install_exception_handlers(application)
    install_openapi_error_examples(application)
    application.include_router(admin_router)
    application.include_router(router)
    return application


app = create_app()
