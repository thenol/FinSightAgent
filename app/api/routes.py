import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.agents.registry import AgentRegistry
from app.analysis.aggregation import ImpactAggregationService
from app.analysis.backfill import ImpactProjectionBackfillService
from app.analysis.forward import ForwardImpactService
from app.analysis.service import ImpactAnalysisService
from app.api.auth import PASSWORD_HASH, require_roles
from app.api.errors import openapi_error_responses
from app.api.login_guard import client_ip_from_request
from app.api.schemas import (
    AdminMetricsResponse,
    AuditLogResponse,
    BriefEntryResponse,
    BriefResponse,
    BudgetLedgerEntryResponse,
    CapabilityEvaluationResponse,
    ClaimResponse,
    ConflictResponse,
    DataEnvelope,
    DocumentUpdateRequest,
    EventDetailResponse,
    EventImpactRelationRequest,
    EventResponse,
    EventTypeProposalResponse,
    EventTypeRegistryResponse,
    EvidenceResponse,
    FactCardResponse,
    ForwardCatalystCreateRequest,
    ForwardImpactWindowCreateRequest,
    FutureEventCreateRequest,
    FutureEventTransitionRequest,
    HistoricalForecastReplayRequest,
    ImpactAnalysisResponse,
    ImpactAnalysisTransitionRequest,
    ImpactGraphEditRequest,
    ImpactGraphLayoutRequest,
    ImpactProjectionBackfillRequest,
    ImpactSnapshotResponse,
    ImpactTargetMappingCreateRequest,
    ImpactTargetMappingSuggestRequest,
    ImpactTargetMappingTransitionRequest,
    ImpactTargetResponse,
    IngestDocumentRequest,
    IngestRunResponse,
    LlmAgentBindingBulkRequest,
    LlmAgentBindingRequest,
    LlmProviderCreateRequest,
    LlmProviderRotateKeyRequest,
    LlmProviderUpdateRequest,
    LoginRequest,
    LoginResponse,
    MarketCalibrationCreateRequest,
    MarketCalibrationTransitionRequest,
    MarketForecastIssueRequest,
    MarketForecastSettlementRequest,
    MarketMasterDataImportPublishRequest,
    MarketMasterDataImportRequest,
    MergeReviewDecisionRequest,
    MergeReviewTaskResponse,
    NodeAttemptResponse,
    OODClusterResponse,
    OODObservationResponse,
    PipelineResponse,
    ReportTransitionRequest,
    ReprocessingJobResponse,
    ResearchBlackboardResponse,
    ResearchCreateRequest,
    ResearchPlanListResponse,
    ResearchPlanResponse,
    ResearchTaskResponse,
    RetrievalRetrieveRequest,
    RetrievalTraceResponse,
    ReviewDecisionRequest,
    ReviewPolicyResponse,
    ReviewPolicyUpdateRequest,
    ReviewTaskResponse,
    SourceCreateRequest,
    SourceHealthResponse,
    SourceResponse,
    SourceUpdateRequest,
    WorkflowCreateRequest,
    WorkflowResponse,
    WorkflowResumeRequest,
)
from app.domain import (
    AuditLog,
    EventImpactRelation,
    ForwardCatalyst,
    ForwardImpactWindow,
    FutureEvent,
    FutureEventRevision,
    FutureEventTargetImpact,
    ImpactTargetMapping,
    MarketCalibrationVersion,
    RetrievalRequest,
    ReviewPolicy,
    Source,
    User,
)
from app.events.type_registry import (
    EventTypeAlreadyDecidedError,
    EventTypeNotFoundError,
    EventTypeRegistryService,
)
from app.ingestion.html_text import scrub_extracted_text
from app.ingestion.seed_sources import seed_sources
from app.market.evaluation import (
    ForecastEvaluationSample,
    compare_forecast_models,
    evaluate_forecasts,
    fit_temperature_scaler,
)
from app.market.factors import EventImpactFactorService
from app.market.forecasting import ForecastLifecycleService, published_calibration_for
from app.market.health import project_provider_health
from app.market.master_data import ImpactTargetMappingService, MarketMasterDataImportService
from app.market.outlook import SUPPORTED_HORIZONS, MarketOutlookService
from app.market.provider import MarketDataProvider
from app.market.quality import MarketQualityService
from app.market.replay import LocalArchiveMarketDataProvider
from app.market.replay_forecasts import HistoricalForecastReplayService
from app.market.state import MarketStateService
from app.model_gateway.config import (
    LlmConfigError,
    bind_all_agents,
    create_provider,
    list_presets,
    public_provider_view,
    rotate_provider_key,
    test_provider,
    update_provider,
    upsert_binding,
)
from app.model_gateway.secrets import SecretBox
from app.ood_learning import OODLearningService
from app.platform.ids import new_id
from app.platform.pagination import decode_cursor, page_items
from app.platform.repository import (
    ApiIdempotencyRecord,
    DocumentNotSoftDeletedError,
    PurgeRetentionWindowError,
    ReportVersionConflict,
    RetentionHoldError,
)
from app.publishing.citations import CitationResolver
from app.publishing.service import FactCardService
from app.retrieval.service import RetrievalService
from app.workflows.dynamic import DynamicWorkflowService
from app.workflows.service import WorkflowService

router = APIRouter()


def _market_data_provider(request: Request) -> MarketDataProvider:
    provider = getattr(request.app.state, "market_data_provider", None)
    if provider is None:
        raise HTTPException(status_code=503, detail="MARKET_DATA_PROVIDER_UNAVAILABLE")
    return provider


def _persisted_evaluation_samples(
    repository,
    *,
    instrument_id: str | None,
    horizon: int | None,
    start: datetime | None,
    end: datetime | None,
    limit: int = 5000,
) -> list[ForecastEvaluationSample]:
    runs = repository.list_market_forecast_runs(
        instrument_id=instrument_id,
        horizon=horizon,
        start=start,
        end=end,
        limit=limit,
    )
    outcomes = {
        item.forecast_id: item
        for item in repository.list_market_forecast_outcomes([run.id for run in runs])
    }
    samples = []
    for run in runs:
        outcome = outcomes.get(run.id)
        eligible = run.probabilities is not None and outcome is not None
        samples.append(
            ForecastEvaluationSample(
                forecast_id=run.id,
                instrument_id=run.instrument_id,
                as_of=run.as_of,
                horizon=run.horizon,
                probabilities=run.probabilities,
                realized_return=outcome.realized_return if outcome else None,
                outcome=outcome.outcome if outcome else None,
                outcome_observed_at=outcome.outcome_observed_at if outcome else None,
                eligible=eligible,
                exclusion_reason=(
                    None
                    if eligible
                    else (
                        "forecast_insufficient_data"
                        if run.probabilities is None
                        else "outcome_not_observed"
                    )
                ),
            )
        )
    return samples


def _persisted_evaluation_samples_by_rule(
    repository,
    *,
    market: str | None,
    horizon: int | None,
    start: datetime | None,
    end: datetime | None,
) -> dict[str, list[ForecastEvaluationSample]]:
    runs = repository.list_market_forecast_runs(horizon=horizon, start=start, end=end, limit=5000)
    if market:
        runs = [run for run in runs if run.instrument_id.startswith(f"{market}:")]
    outcomes = {
        item.forecast_id: item
        for item in repository.list_market_forecast_outcomes([run.id for run in runs])
    }
    values: dict[str, list[ForecastEvaluationSample]] = {}
    for run in runs:
        outcome = outcomes.get(run.id)
        eligible = run.probabilities is not None and outcome is not None
        values.setdefault(run.rule_version, []).append(
            ForecastEvaluationSample(
                forecast_id=run.id,
                instrument_id=run.instrument_id,
                as_of=run.as_of,
                horizon=run.horizon,
                probabilities=run.probabilities,
                realized_return=outcome.realized_return if outcome else None,
                outcome=outcome.outcome if outcome else None,
                outcome_observed_at=outcome.outcome_observed_at if outcome else None,
                eligible=eligible,
                exclusion_reason=(
                    None
                    if eligible
                    else "forecast_insufficient_data"
                    if run.probabilities is None
                    else "outcome_not_observed"
                ),
            )
        )
    return values


@router.get("/api/v1/market/capabilities", response_model=DataEnvelope)
def market_capabilities(
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    capability = _market_data_provider(request).capability
    return DataEnvelope(
        data=jsonable_encoder(capability),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/providers/health", response_model=DataEnvelope)
def market_provider_health(
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    return DataEnvelope(
        data=jsonable_encoder(project_provider_health(_market_data_provider(request))),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/instruments", response_model=DataEnvelope)
def market_instruments(
    request: Request,
    market: Optional[str] = Query(default=None, pattern="^(cn|hk|us)$"),  # noqa: B008
    instrument_type: Optional[str] = Query(default=None, pattern="^(index|sector|etf|stock)$"),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    catalog = getattr(request.app.state, "market_instruments", None)
    if catalog is None:
        raise HTTPException(status_code=503, detail="MARKET_INSTRUMENT_CATALOG_UNAVAILABLE")
    return DataEnvelope(
        data=jsonable_encoder(catalog.list(market=market, instrument_type=instrument_type)),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/industry-taxonomies", response_model=DataEnvelope)
def market_industry_taxonomies(
    request: Request,
    status_filter: Optional[str] = Query(default=None, alias="status"),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    values = request.app.state.repository.list_industry_taxonomies(status_filter)
    return DataEnvelope(
        data=jsonable_encoder(values),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/industry-classifications", response_model=DataEnvelope)
def market_industry_classifications(
    request: Request,
    taxonomy_id: Optional[str] = Query(default=None),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    values = request.app.state.repository.list_industry_classifications(taxonomy_id)
    return DataEnvelope(
        data=jsonable_encoder(values),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/master-data/imports", response_model=DataEnvelope)
def list_market_master_data_imports(
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    values = request.app.state.repository.list_market_master_data_import_runs()
    return DataEnvelope(
        data=jsonable_encoder(values),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.post("/api/v1/market/master-data/imports", response_model=DataEnvelope)
def stage_market_master_data_import(
    payload: MarketMasterDataImportRequest,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    run = MarketMasterDataImportService(request.app.state.repository).stage(
        standard=payload.standard,
        version=payload.version,
        name=payload.name,
        source=payload.source,
        effective_from=payload.effective_from,
        classifications=[item.model_dump() for item in payload.classifications],
        memberships=[item.model_dump() for item in payload.memberships],
        source_metadata=payload.source_metadata,
        created_by=user.id,
    )
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="market_master_data.import_staged",
            object_type="market_master_data_import",
            object_id=run.id,
            request_id=request.state.request_id,
            details={"status": run.status, "errors": run.errors},
        )
    )
    return DataEnvelope(
        data=jsonable_encoder(run),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.post(
    "/api/v1/market/master-data/imports/{run_id}/publish",
    response_model=DataEnvelope,
)
def publish_market_master_data_import(
    run_id: str,
    payload: MarketMasterDataImportPublishRequest,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    try:
        run = MarketMasterDataImportService(request.app.state.repository).publish(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="MARKET_MASTER_IMPORT_NOT_FOUND") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="market_master_data.import_published",
            object_type="market_master_data_import",
            object_id=run.id,
            request_id=request.state.request_id,
            details={"reason": payload.reason, "source_hash": run.source_hash},
        )
    )
    return DataEnvelope(
        data=jsonable_encoder(run),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/impact-target-mappings", response_model=DataEnvelope)
def list_impact_target_mappings(
    request: Request,
    target_id: Optional[str] = Query(default=None),  # noqa: B008
    status_filter: Optional[str] = Query(default=None, alias="status"),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    values = request.app.state.repository.list_impact_target_mappings(target_id, status_filter)
    return DataEnvelope(
        data=jsonable_encoder(values),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.post("/api/v1/market/impact-target-mappings", response_model=DataEnvelope)
def create_impact_target_mapping(
    payload: ImpactTargetMappingCreateRequest,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    if repository.get_impact_target(payload.target_id) is None:
        raise HTTPException(status_code=404, detail="IMPACT_TARGET_NOT_FOUND")
    if payload.valid_from and payload.valid_to and payload.valid_to < payload.valid_from:
        raise HTTPException(status_code=422, detail="IMPACT_TARGET_MAPPING_RANGE_INVALID")
    if payload.mapping_type == "instrument":
        valid_code = repository.get_market_instrument(payload.mapping_code) is not None
    elif payload.mapping_type == "industry":
        valid_code = any(
            item.code == payload.mapping_code for item in repository.list_industry_classifications()
        )
    else:
        valid_code = payload.mapping_code in {"cn", "hk", "us"}
    if not valid_code:
        raise HTTPException(status_code=422, detail="IMPACT_TARGET_MAPPING_CODE_INVALID")
    duplicates = repository.list_impact_target_mappings(payload.target_id)
    if any(
        item.mapping_type == payload.mapping_type and item.mapping_code == payload.mapping_code
        for item in duplicates
    ):
        raise HTTPException(status_code=409, detail="IMPACT_TARGET_MAPPING_CONFLICT")
    value = ImpactTargetMapping(
        id=new_id("itm"),
        target_id=payload.target_id,
        mapping_type=payload.mapping_type,
        mapping_code=payload.mapping_code,
        weight=payload.weight,
        confidence=payload.confidence,
        status="proposed",
        reason=payload.reason,
        source="manual",
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        created_by=user.id,
        created_at=datetime.now(timezone.utc),
    )
    repository.save_impact_target_mapping(value)
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="impact_target_mapping.proposed",
            object_type="impact_target_mapping",
            object_id=value.id,
            request_id=request.state.request_id,
            details={"target_id": value.target_id, "reason": payload.reason},
        )
    )
    return DataEnvelope(
        data=jsonable_encoder(value),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.post("/api/v1/market/impact-target-mappings/suggest", response_model=DataEnvelope)
def suggest_impact_target_mappings(
    payload: ImpactTargetMappingSuggestRequest,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    try:
        values = ImpactTargetMappingService(request.app.state.repository).suggest(
            target_id=payload.target_id, created_by=user.id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="IMPACT_TARGET_NOT_FOUND") from exc
    return DataEnvelope(
        data=jsonable_encoder(values),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.post(
    "/api/v1/market/impact-target-mappings/{mapping_id}/transition",
    response_model=DataEnvelope,
)
def transition_impact_target_mapping(
    mapping_id: str,
    payload: ImpactTargetMappingTransitionRequest,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    try:
        value = ImpactTargetMappingService(request.app.state.repository).transition(
            mapping_id, status=payload.status, reviewed_by=user.id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="IMPACT_TARGET_MAPPING_NOT_FOUND") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="IMPACT_TARGET_MAPPING_TRANSITION_INVALID"
        ) from exc
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action=f"impact_target_mapping.{payload.status}",
            object_type="impact_target_mapping",
            object_id=value.id,
            request_id=request.state.request_id,
            details={"reason": payload.reason},
        )
    )
    return DataEnvelope(
        data=jsonable_encoder(value),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/calendar", response_model=DataEnvelope)
def market_calendar(
    request: Request,
    market: str = Query(..., pattern="^(cn|hk|us)$"),  # noqa: B008
    start: date = Query(),  # noqa: B008
    end: date = Query(),  # noqa: B008
    as_of: Optional[datetime] = Query(default=None),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    if end < start:
        raise HTTPException(status_code=422, detail="MARKET_CALENDAR_RANGE_INVALID")
    calendar = getattr(request.app.state, "market_calendar", None)
    if calendar is None:
        raise HTTPException(status_code=503, detail="MARKET_CALENDAR_UNAVAILABLE")
    result = calendar.query(
        market=market,
        start=start,
        end=end,
        as_of=as_of or datetime.now(timezone.utc),
    )
    return DataEnvelope(
        data=jsonable_encoder(result),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/bars", response_model=DataEnvelope)
def market_bars(
    request: Request,
    instrument_ids: list[str] = Query(min_length=1, max_length=100),  # noqa: B008
    start: datetime = Query(),  # noqa: B008
    end: datetime = Query(),  # noqa: B008
    interval: str = Query(default="1d", pattern="^(5m|1d)$"),  # noqa: B008
    as_of: Optional[datetime] = Query(default=None),  # noqa: B008
    limit: int = Query(default=5000, ge=1, le=100_000),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    if end < start:
        raise HTTPException(status_code=422, detail="MARKET_DATA_RANGE_INVALID")
    effective_as_of = as_of or datetime.now(timezone.utc)
    if effective_as_of.tzinfo is None:
        raise HTTPException(status_code=422, detail="MARKET_DATA_AS_OF_TIMEZONE_REQUIRED")
    try:
        result = _market_data_provider(request).get_bars(
            instrument_ids=instrument_ids,
            start=start,
            end=end,
            interval=interval,
            as_of=effective_as_of,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="MARKET_DATA_QUERY_INVALID") from exc
    return DataEnvelope(
        data=jsonable_encoder(result),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/snapshots", response_model=DataEnvelope)
def market_snapshots(
    request: Request,
    instrument_ids: list[str] = Query(min_length=1, max_length=100),  # noqa: B008
    as_of: Optional[datetime] = Query(default=None),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    effective_as_of = as_of or datetime.now(timezone.utc)
    if effective_as_of.tzinfo is None:
        raise HTTPException(status_code=422, detail="MARKET_DATA_AS_OF_TIMEZONE_REQUIRED")
    try:
        result = _market_data_provider(request).get_snapshots(
            instrument_ids=instrument_ids,
            as_of=effective_as_of,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="MARKET_DATA_QUERY_INVALID") from exc
    return DataEnvelope(
        data=jsonable_encoder(result),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/states", response_model=DataEnvelope)
def market_states(
    request: Request,
    instrument_ids: list[str] = Query(min_length=1, max_length=100),  # noqa: B008
    start: datetime = Query(),  # noqa: B008
    end: datetime = Query(),  # noqa: B008
    interval: str = Query(default="1d", pattern="^(5m|1d)$"),  # noqa: B008
    as_of: Optional[datetime] = Query(default=None),  # noqa: B008
    limit: int = Query(default=250, ge=3, le=5000),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    if end < start:
        raise HTTPException(status_code=422, detail="MARKET_DATA_RANGE_INVALID")
    effective_as_of = as_of or datetime.now(timezone.utc)
    try:
        states = MarketStateService(
            _market_data_provider(request), getattr(request.app.state, "market_calendar", None)
        ).calculate(
            instrument_ids=instrument_ids,
            start=start,
            end=end,
            interval=interval,
            as_of=effective_as_of,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="MARKET_STATE_QUERY_INVALID") from exc
    return DataEnvelope(
        data=jsonable_encoder(states),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/factors", response_model=DataEnvelope)
def market_factors(
    request: Request,
    instrument_ids: list[str] = Query(min_length=1, max_length=100),  # noqa: B008
    horizon: int = Query(default=1, description="Forecast horizon in trading days"),  # noqa: B008
    as_of: Optional[datetime] = Query(default=None),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    if horizon not in SUPPORTED_HORIZONS:
        raise HTTPException(status_code=422, detail="MARKET_OUTLOOK_HORIZON_UNSUPPORTED")
    effective_as_of = as_of or datetime.now(timezone.utc)
    if effective_as_of.tzinfo is None:
        raise HTTPException(status_code=422, detail="MARKET_DATA_AS_OF_TIMEZONE_REQUIRED")
    catalog = request.app.state.market_instruments
    instruments = [catalog.get(instrument_id) for instrument_id in instrument_ids]
    missing = [
        instrument_id
        for instrument_id, instrument in zip(instrument_ids, instruments, strict=True)
        if instrument is None
    ]
    if missing:
        raise HTTPException(status_code=404, detail="MARKET_INSTRUMENT_NOT_FOUND")
    service = EventImpactFactorService(request.app.state.repository)
    factors = [
        service.snapshot(instrument, as_of=effective_as_of, horizon=horizon)
        for instrument in instruments
        if instrument is not None
    ]
    return DataEnvelope(
        data=jsonable_encoder(factors),
        meta={
            "request_id": request.state.request_id,
            "schema_version": "1.0",
            "rule_version": "forecast-factor-v1",
            "as_of": effective_as_of.isoformat(),
        },
    )


@router.get("/api/v1/market/outlooks", response_model=DataEnvelope)
def market_outlooks(
    request: Request,
    instrument_ids: list[str] = Query(min_length=1, max_length=100),  # noqa: B008
    start: datetime = Query(),  # noqa: B008
    end: datetime = Query(),  # noqa: B008
    horizon: int = Query(default=1, description="Forecast horizon in trading days"),  # noqa: B008
    interval: str = Query(default="1d", pattern="^(5m|1d)$"),  # noqa: B008
    as_of: Optional[datetime] = Query(default=None),  # noqa: B008
    limit: int = Query(default=250, ge=3, le=5000),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    if horizon not in SUPPORTED_HORIZONS:
        raise HTTPException(status_code=422, detail="MARKET_OUTLOOK_HORIZON_UNSUPPORTED")
    if end < start:
        raise HTTPException(status_code=422, detail="MARKET_DATA_RANGE_INVALID")
    effective_as_of = as_of or datetime.now(timezone.utc)
    if effective_as_of.tzinfo is None:
        raise HTTPException(status_code=422, detail="MARKET_DATA_AS_OF_TIMEZONE_REQUIRED")
    try:
        states = MarketStateService(
            _market_data_provider(request), getattr(request.app.state, "market_calendar", None)
        ).calculate(
            instrument_ids=instrument_ids,
            start=start,
            end=end,
            interval=interval,
            as_of=effective_as_of,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="MARKET_OUTLOOK_QUERY_INVALID") from exc
    factor_service = EventImpactFactorService(request.app.state.repository)
    catalog = request.app.state.market_instruments
    outlooks = []
    for state in states:
        instrument = catalog.get(state.instrument_id)
        event_factor = (
            factor_service.snapshot(instrument, as_of=effective_as_of, horizon=horizon)
            if instrument is not None
            else None
        )
        outlooks.append(
            MarketOutlookService().preview(
                state,
                horizon=horizon,
                event_factor=event_factor,
                calibration=published_calibration_for(
                    request.app.state.repository,
                    state.instrument_id,
                    horizon,
                    as_of=effective_as_of,
                ),
            )
        )
    return DataEnvelope(
        data=jsonable_encoder(outlooks),
        meta={
            "request_id": request.state.request_id,
            "schema_version": "2.1",
            "rule_version": "outlook-baseline-v2",
            "calibration_status": "uncalibrated",
            "factor_rule_version": "forecast-factor-v1",
        },
    )


@router.post("/api/v1/market/forecast-runs", response_model=DataEnvelope)
def issue_market_forecast_runs(
    payload: MarketForecastIssueRequest,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    if payload.horizon not in SUPPORTED_HORIZONS:
        raise HTTPException(status_code=422, detail="MARKET_OUTLOOK_HORIZON_UNSUPPORTED")
    if payload.end < payload.start:
        raise HTTPException(status_code=422, detail="MARKET_DATA_RANGE_INVALID")
    effective_as_of = payload.as_of or datetime.now(timezone.utc)
    if any(value.tzinfo is None for value in (payload.start, payload.end, effective_as_of)):
        raise HTTPException(status_code=422, detail="MARKET_DATA_AS_OF_TIMEZONE_REQUIRED")
    catalog = request.app.state.market_instruments
    if any(catalog.get(instrument_id) is None for instrument_id in payload.instrument_ids):
        raise HTTPException(status_code=404, detail="MARKET_INSTRUMENT_NOT_FOUND")
    try:
        receipt = ForecastLifecycleService(
            request.app.state.repository,
            _market_data_provider(request),
            catalog,
            getattr(request.app.state, "market_calendar", None),
        ).issue(
            instrument_ids=payload.instrument_ids,
            start=payload.start,
            end=payload.end,
            horizon=payload.horizon,
            interval=payload.interval,
            as_of=effective_as_of,
            limit=payload.limit,
            created_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="MARKET_FORECAST_ISSUE_INVALID") from exc
    return DataEnvelope(
        data=jsonable_encoder(receipt.runs),
        meta={
            "request_id": request.state.request_id,
            "schema_version": "1.0",
            "created_count": receipt.created_count,
            "reused_count": receipt.reused_count,
        },
    )


@router.post("/api/v1/market/forecast-replays", response_model=DataEnvelope)
def replay_historical_market_forecasts(
    payload: HistoricalForecastReplayRequest,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    if payload.horizon not in SUPPORTED_HORIZONS:
        raise HTTPException(status_code=422, detail="MARKET_OUTLOOK_HORIZON_UNSUPPORTED")
    if payload.forecast_to < payload.forecast_from:
        raise HTTPException(status_code=422, detail="MARKET_FORECAST_REPLAY_RANGE_INVALID")
    try:
        receipt = HistoricalForecastReplayService(
            request.app.state.repository,
            LocalArchiveMarketDataProvider(
                request.app.state.settings.market_archive_root,
                require_verified=True,
            ),
            request.app.state.market_instruments,
            request.app.state.market_calendar,
        ).run(
            instrument_ids=payload.instrument_ids,
            forecast_from=payload.forecast_from,
            forecast_to=payload.forecast_to,
            horizon=payload.horizon,
            lookback_days=payload.lookback_days,
            publication_lag_minutes=payload.publication_lag_minutes,
            max_slots=payload.max_slots,
            created_by=user.id,
            settle_outcomes=payload.settle_outcomes,
            evaluation_as_of=payload.evaluation_as_of,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="MARKET_FORECAST_REPLAY_INVALID") from exc
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="market_forecast.historical_replay",
            object_type="market_forecast_run",
            object_id=None,
            request_id=request.state.request_id,
            details={
                "forecast_from": payload.forecast_from.isoformat(),
                "forecast_to": payload.forecast_to.isoformat(),
                "instrument_ids": payload.instrument_ids,
                "horizon": payload.horizon,
                "source_provider": receipt.source_provider,
                "created_count": receipt.created_count,
                "reused_count": receipt.reused_count,
                "insufficient_count": receipt.insufficient_count,
                "settled_count": receipt.settled_count,
                "pending_outcome_count": receipt.pending_outcome_count,
                "status": receipt.status,
            },
            created_at=datetime.now(timezone.utc),
        )
    )
    return DataEnvelope(
        data=jsonable_encoder(receipt),
        meta={
            "request_id": request.state.request_id,
            "schema_version": "1.0",
            "rule_version": receipt.rule_version,
        },
    )


@router.get("/api/v1/market/forecast-runs", response_model=DataEnvelope)
def list_market_forecast_runs(
    request: Request,
    instrument_id: Optional[str] = Query(default=None),  # noqa: B008
    horizon: Optional[int] = Query(default=None),  # noqa: B008
    start: Optional[datetime] = Query(default=None),  # noqa: B008
    end: Optional[datetime] = Query(default=None),  # noqa: B008
    limit: int = Query(default=500, ge=1, le=5000),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    runs = request.app.state.repository.list_market_forecast_runs(
        instrument_id, horizon, start, end, limit
    )
    outcomes = {
        item.forecast_id: item
        for item in request.app.state.repository.list_market_forecast_outcomes(
            [run.id for run in runs]
        )
    }
    return DataEnvelope(
        data=[
            {**jsonable_encoder(run), "outcome": jsonable_encoder(outcomes.get(run.id))}
            for run in runs
        ],
        meta={
            "request_id": request.state.request_id,
            "schema_version": "1.0",
            "count": len(runs),
        },
    )


@router.post("/api/v1/market/forecast-outcomes/settle", response_model=DataEnvelope)
def settle_market_forecast_outcomes(
    payload: MarketForecastSettlementRequest,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    effective_as_of = payload.evaluation_as_of or datetime.now(timezone.utc)
    try:
        receipt = ForecastLifecycleService(
            request.app.state.repository,
            _market_data_provider(request),
            request.app.state.market_instruments,
            getattr(request.app.state, "market_calendar", None),
        ).settle(
            evaluation_as_of=effective_as_of,
            forecast_ids=payload.forecast_ids,
            flat_band=payload.flat_band,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="MARKET_FORECAST_SETTLEMENT_INVALID") from exc
    return DataEnvelope(
        data=jsonable_encoder(receipt),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/evaluations", response_model=DataEnvelope)
def get_market_forecast_evaluation(
    request: Request,
    instrument_id: Optional[str] = Query(default=None),  # noqa: B008
    market: Optional[str] = Query(default=None, pattern="^(cn|hk|us)$"),  # noqa: B008
    horizon: Optional[int] = Query(default=None),  # noqa: B008
    start: Optional[datetime] = Query(default=None),  # noqa: B008
    end: Optional[datetime] = Query(default=None),  # noqa: B008
    bin_count: int = Query(default=10, ge=2, le=20),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    samples = _persisted_evaluation_samples(
        request.app.state.repository,
        instrument_id=instrument_id,
        horizon=horizon,
        start=start,
        end=end,
    )
    if market is not None:
        samples = [item for item in samples if item.instrument_id.startswith(f"{market}:")]
    report = evaluate_forecasts(samples, bin_count=bin_count)
    return DataEnvelope(
        data={
            "report": jsonable_encoder(report),
            "exclusions": {
                reason: sum(item.exclusion_reason == reason for item in samples)
                for reason in sorted(
                    {item.exclusion_reason for item in samples if item.exclusion_reason}
                )
            },
        },
        meta={
            "request_id": request.state.request_id,
            "schema_version": "1.0",
            "rule_version": "forecast-evaluation-v1",
        },
    )


@router.get("/api/v1/market/model-comparisons", response_model=DataEnvelope)
def get_market_model_comparison(
    request: Request,
    market: Optional[str] = Query(default=None, pattern="^(cn|hk|us)$"),  # noqa: B008
    horizon: Optional[int] = Query(default=None),  # noqa: B008
    start: Optional[datetime] = Query(default=None),  # noqa: B008
    end: Optional[datetime] = Query(default=None),  # noqa: B008
    incumbent_model_key: Optional[str] = Query(default=None),  # noqa: B008
    minimum_comparable_samples: int = Query(default=100, ge=20, le=10000),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    samples_by_rule = _persisted_evaluation_samples_by_rule(
        request.app.state.repository,
        market=market,
        horizon=horizon,
        start=start,
        end=end,
    )
    comparison = compare_forecast_models(
        samples_by_rule,
        incumbent_model_key=incumbent_model_key,
        minimum_comparable_samples=minimum_comparable_samples,
    )
    return DataEnvelope(
        data=jsonable_encoder(comparison),
        meta={
            "request_id": request.state.request_id,
            "schema_version": "1.0",
            "rule_version": "forecast-champion-challenger-v1",
        },
    )


@router.post("/api/v1/market/calibrations", response_model=DataEnvelope)
def create_market_calibration(
    payload: MarketCalibrationCreateRequest,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    if payload.horizon not in SUPPORTED_HORIZONS:
        raise HTTPException(status_code=422, detail="MARKET_OUTLOOK_HORIZON_UNSUPPORTED")
    if payload.train_end <= payload.train_start:
        raise HTTPException(status_code=422, detail="MARKET_CALIBRATION_RANGE_INVALID")
    duplicates = request.app.state.repository.list_market_calibration_versions(
        payload.model_key, payload.market, payload.horizon
    )
    if any(item.version == payload.version for item in duplicates):
        raise HTTPException(status_code=409, detail="MARKET_CALIBRATION_VERSION_CONFLICT")
    samples = _persisted_evaluation_samples(
        request.app.state.repository,
        instrument_id=payload.instrument_id,
        horizon=payload.horizon,
        start=payload.train_start,
        end=payload.train_end,
    )
    if payload.market != "all":
        samples = [item for item in samples if item.instrument_id.startswith(f"{payload.market}:")]
    fitted = fit_temperature_scaler(samples)
    if fitted.status != "fitted":
        raise HTTPException(status_code=422, detail="MARKET_CALIBRATION_SAMPLE_INSUFFICIENT")
    report = evaluate_forecasts(samples)
    calibration = MarketCalibrationVersion(
        id=new_id("mcv"),
        model_key=payload.model_key,
        version=payload.version,
        horizon=payload.horizon,
        market=payload.market,
        status="draft",
        method="temperature_scaling",
        parameters={"temperature": fitted.temperature},
        metrics=jsonable_encoder(report),
        train_start=payload.train_start,
        train_end=payload.train_end,
        sample_count=fitted.fit_sample_count,
        created_by=user.id,
        created_at=datetime.now(timezone.utc),
    )
    request.app.state.repository.save_market_calibration_version(calibration)
    return DataEnvelope(
        data=jsonable_encoder(calibration),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/calibrations", response_model=DataEnvelope)
def list_market_calibrations(
    request: Request,
    model_key: Optional[str] = Query(default=None),  # noqa: B008
    market: Optional[str] = Query(default=None),  # noqa: B008
    horizon: Optional[int] = Query(default=None),  # noqa: B008
    status_filter: Optional[str] = Query(default=None, alias="status"),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    values = request.app.state.repository.list_market_calibration_versions(
        model_key, market, horizon, status_filter
    )
    return DataEnvelope(
        data=jsonable_encoder(values),
        meta={
            "request_id": request.state.request_id,
            "schema_version": "1.0",
            "count": len(values),
        },
    )


@router.post(
    "/api/v1/market/calibrations/{calibration_id}/transition",
    response_model=DataEnvelope,
)
def transition_market_calibration(
    calibration_id: str,
    payload: MarketCalibrationTransitionRequest,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    calibration = repository.get_market_calibration_version(calibration_id)
    if calibration is None:
        raise HTTPException(status_code=404, detail="MARKET_CALIBRATION_NOT_FOUND")
    allowed = {"draft": {"published"}, "published": {"retired"}, "retired": set()}
    if payload.status not in allowed.get(calibration.status, set()):
        raise HTTPException(status_code=409, detail="MARKET_CALIBRATION_TRANSITION_INVALID")
    now = datetime.now(timezone.utc)
    if payload.status == "published":
        metrics = calibration.metrics
        gates = {
            "sample_count": calibration.sample_count >= 200,
            "coverage": float(metrics.get("coverage") or 0.0) >= 0.75,
            "brier_score": (
                float(metrics["brier_score"]) if metrics.get("brier_score") is not None else 99.0
            )
            <= 0.75,
            "expected_calibration_error": (
                (
                    float(metrics["expected_calibration_error"])
                    if metrics.get("expected_calibration_error") is not None
                    else 99.0
                )
                <= 0.10
            ),
            "train_period_closed": calibration.train_end <= now,
        }
        failed = [key for key, passed in gates.items() if not passed]
        if failed:
            raise HTTPException(
                status_code=409,
                detail=f"MARKET_CALIBRATION_QUALITY_GATE_FAILED:{','.join(failed)}",
            )
        published = repository.list_market_calibration_versions(
            calibration.model_key,
            calibration.market,
            calibration.horizon,
            "published",
        )
        for previous in published:
            repository.update_market_calibration_version(replace(previous, status="retired"))
    updated = replace(
        calibration,
        status=payload.status,
        published_at=now if payload.status == "published" else calibration.published_at,
    )
    repository.update_market_calibration_version(updated)
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action=f"market_calibration.{payload.status}",
            object_type="market_calibration_version",
            object_id=updated.id,
            request_id=request.state.request_id,
            details={"reason": payload.reason, "version": updated.version},
            created_at=now,
        )
    )
    return DataEnvelope(
        data=jsonable_encoder(updated),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/market/quality", response_model=DataEnvelope)
def market_quality(
    request: Request,
    instrument_ids: list[str] = Query(min_length=1, max_length=100),  # noqa: B008
    start: datetime = Query(),  # noqa: B008
    end: datetime = Query(),  # noqa: B008
    interval: str = Query(default="1d", pattern="^(5m|1d)$"),  # noqa: B008
    as_of: Optional[datetime] = Query(default=None),  # noqa: B008
    limit: int = Query(default=250, ge=3, le=5000),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    if end < start:
        raise HTTPException(status_code=422, detail="MARKET_DATA_RANGE_INVALID")
    effective_as_of = as_of or datetime.now(timezone.utc)
    if effective_as_of.tzinfo is None:
        raise HTTPException(status_code=422, detail="MARKET_DATA_AS_OF_TIMEZONE_REQUIRED")
    try:
        result = MarketQualityService(
            _market_data_provider(request), getattr(request.app.state, "market_calendar", None)
        ).assess(
            instrument_ids=instrument_ids,
            start=start,
            end=end,
            interval=interval,
            as_of=effective_as_of,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="MARKET_QUALITY_QUERY_INVALID") from exc
    return DataEnvelope(
        data=jsonable_encoder(result),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


BUSINESS_ROLES = ("researcher", "reviewer", "publisher", "admin")
MAX_PAGE_SIZE = 200


def _validate_query(request: Request, allowed: set[str]) -> None:
    unknown = set(request.query_params) - allowed
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"QUERY_PARAMETER_NOT_ALLOWED:{','.join(sorted(unknown))}",
        )


def _validate_cursor(cursor: Optional[str]) -> None:
    if cursor:
        try:
            decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="INVALID_CURSOR") from exc


def _page_envelope(
    values: list[Any],
    limit: int,
    timestamp_of,
    request_id: str,
) -> DataEnvelope:
    page, next_cursor = page_items(values, limit, timestamp_of)
    response = envelope(page, request_id)
    response.meta["next_cursor"] = next_cursor
    return response


def _idempotency_request_hash(operation: str, payload: object) -> str:
    from app.model_gateway.secrets import redact_sensitive_mapping

    safe_payload = redact_sensitive_mapping(jsonable_encoder(payload))
    encoded = json.dumps(
        {"operation": operation, "payload": safe_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _idempotency_begin(
    request: Request,
    user: User,
    key: Optional[str],
    operation: str,
    payload: object,
) -> tuple[Optional[str], str, Optional[DataEnvelope]]:
    request_hash = _idempotency_request_hash(operation, payload)
    if key is None:
        return None, request_hash, None
    if not key.strip() or len(key) > 128:
        raise HTTPException(status_code=400, detail="IDEMPOTENCY_KEY_INVALID")
    storage_key = f"api:{user.id}:{operation}:{key}"
    previous = request.app.state.repository.get_api_idempotent(storage_key)
    if previous:
        if previous.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT")
        return storage_key, request_hash, DataEnvelope.model_validate(previous.response)
    return storage_key, request_hash, None


def _idempotency_finish(
    request: Request,
    storage_key: Optional[str],
    request_hash: str,
    operation: str,
    resource_id: str,
    response: DataEnvelope,
) -> DataEnvelope:
    if storage_key:
        request.app.state.repository.save_api_idempotent(
            storage_key,
            ApiIdempotencyRecord(
                request_hash=request_hash,
                operation=operation,
                resource_id=resource_id,
                response=jsonable_encoder(response),
            ),
        )
    return response


@router.post(
    "/api/v1/auth/login",
    response_model=DataEnvelope,
    responses=openapi_error_responses(401, 422, 429),
)
def login(payload: LoginRequest, request: Request) -> DataEnvelope:
    guard = request.app.state.login_guard
    client_key = client_ip_from_request(
        request.headers.get("X-Forwarded-For"),
        request.client.host if request.client else None,
    )
    allowed, retry_after = guard.check_allowed(client_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="AUTH_LOGIN_LOCKED",
            headers={"Retry-After": str(retry_after or 1)},
        )
    user = request.app.state.repository.get_user_by_username(payload.username)
    if (
        not user
        or user.status != "active"
        or not PASSWORD_HASH.verify(payload.password, user.password_hash)
    ):
        guard.record_failure(client_key)
        raise HTTPException(status_code=401, detail="AUTH_INVALID_CREDENTIALS")
    guard.record_success(client_key)
    token = request.app.state.token_manager.issue(user)
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="auth.login",
            object_type="user",
            object_id=user.id,
            request_id=request.state.request_id,
            details={},
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(LoginResponse(access_token=token, expires_in=3600), request.state.request_id)


@router.get("/api/v1/sources", response_model=DataEnvelope)
def list_sources(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Optional[str] = None,
    _user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"cursor", "limit"})
    _validate_cursor(cursor)
    values = [
        SourceResponse(**source.__dict__)
        for source in request.app.state.repository.list_sources(limit=limit + 1, cursor=cursor)
    ]
    return _page_envelope(values, limit, lambda _: None, request.state.request_id)


@router.get("/api/v1/audit-logs", response_model=DataEnvelope)
def list_audit_logs(
    request: Request,
    cursor: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    _user: User = Depends(require_roles("reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"cursor", "limit"})
    _validate_cursor(cursor)
    values = [
        AuditLogResponse(**log.__dict__)
        for log in request.app.state.repository.list_audit_logs(limit=limit + 1, cursor=cursor)
    ]
    return _page_envelope(values, limit, lambda value: value.created_at, request.state.request_id)


@router.get("/api/v1/reviews", response_model=DataEnvelope)
def list_reviews(
    request: Request,
    status_filter: Annotated[Optional[str], Query(pattern="^(pending|decided)$")] = None,
    cursor: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    _user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"status_filter", "cursor", "limit"})
    _validate_cursor(cursor)
    tasks = request.app.state.repository.list_review_tasks(
        status_filter, limit=limit + 1, cursor=cursor
    )
    values = [ReviewTaskResponse.model_validate(task, from_attributes=True) for task in tasks]
    return _page_envelope(values, limit, lambda value: value.created_at, request.state.request_id)


def _review_queue_state(task, attempts, now: datetime) -> str:
    if task.status == "decided":
        return "decided"
    latest = attempts[0] if attempts else None
    if latest and latest.status == "decided":
        return "agent_decided"
    if latest and latest.status in {"escalated", "disabled"}:
        return "escalated_to_human"
    age = (now - (task.created_at or now)).total_seconds()
    return "sla_breached" if age > 3600 else "pending"


def _review_queue_display(
    repository, object_type: str, object_id: str, cache: dict
) -> tuple[dict, dict]:
    """Resolve a review object's business-facing label without exposing IDs as the title."""
    key = (object_type, object_id)
    if key in cache:
        return cache[key]
    display = {
        "title": f"{object_type} 审核对象",
        "type_label": {
            "report": "研究报告",
            "workflow": "工作流",
            "claim_conflict": "事实冲突",
            "merge_review": "事件合并审核",
        }.get(object_type, object_type),
        "subtitle": "对象详情暂不可用",
        "summary": "请打开详情查看审核上下文。",
        "href": f"/reviews/{object_id}",
        "reference_id": object_id,
    }
    context: dict = {}
    if object_type == "merge_review":
        task = repository.get_merge_review_task(object_id)
        document = repository.get_document(task.document_id) if task else None
        events = [repository.get_event(event_id) for event_id in (task.candidates if task else [])]
        events = [event for event in events if event]
        if document:
            display.update(
                title=document.title or "待确认文档",
                subtitle=f"{display['type_label']} · {len(events)} 个候选事件",
                summary=(document.content or "").strip()[:240] or "文档等待事件归并判断。",
                href=f"/merge-reviews/{object_id}",
            )
        if events:
            event = events[0]
            context.update(
                event_id=event.id,
                event_title=event.title,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                importance=event.importance,
                candidate_count=len(events),
            )
    else:
        obj = (
            repository.get_fact_card(object_id)
            if object_type == "report"
            else repository.get_workflow_run(object_id)
            if object_type == "workflow"
            else repository.get_conflict(object_id)
        )
        event_id = getattr(obj, "event_id", None) if obj else None
        event = repository.get_event(event_id) if event_id else None
        if object_type == "report" and obj:
            display.update(
                title=obj.title,
                subtitle=f"{display['type_label']} · {obj.report_type}",
                summary=obj.summary or "报告待审核。",
                href=f"/reports/{object_id}",
            )
        elif object_type == "workflow" and obj:
            display.update(
                title=event.title if event else "工作流运行",
                subtitle=f"{display['type_label']} · {obj.current_node or '待执行'}",
                summary=obj.error_code or f"当前状态：{obj.status}",
            )
        elif object_type == "claim_conflict" and obj:
            display.update(
                title=obj.summary or "事实冲突待处理",
                subtitle=f"{display['type_label']} · {obj.conflict_type} · {obj.severity}",
                summary=f"涉及 {len(obj.claim_ids)} 条 Claim。",
                href=f"/conflicts/{object_id}",
            )
        if event:
            context.update(
                event_id=event.id,
                event_title=event.title,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                importance=event.importance,
            )
    cache[key] = (display, context)
    return display, context


def _review_queue_item(repository, task, now: datetime, cache: Optional[dict] = None) -> dict:
    cache = cache if cache is not None else {}
    object_type = getattr(task, "object_type", "merge_review")
    object_id = getattr(task, "object_id", getattr(task, "id", ""))
    attempts = repository.list_auto_review_attempts(task.id, limit=20)
    latest = attempts[0] if attempts else None
    display, context = _review_queue_display(repository, object_type, object_id, cache)
    reason_code = getattr(task, "reason_code", "EVENT_MERGE_CANDIDATE")
    status = getattr(task, "status", "pending")
    risk_level = (
        "high"
        if reason_code in {"CLAIM_CONFLICT", "QUALITY_GATE_FAILED", "LOW_CONFIDENCE"}
        else "normal"
    )
    importance = float(context.get("importance") or 0)
    sla_factor = 1.5 if (now - (task.created_at or now)).total_seconds() > 3600 else 1.0
    priority_score = min(
        100, round((importance * 60 + (30 if risk_level == "high" else 10)) * sla_factor)
    )
    return {
        **task.__dict__,
        "object_type": object_type,
        "object_id": object_id,
        "reason_code": reason_code,
        "status": "pending" if object_type == "merge_review" and status == "open" else status,
        "allowed_decisions": getattr(task, "allowed_decisions", ["merge", "new_event", "skip"]),
        "reviewer_id": getattr(task, "reviewer_id", None),
        "decided_at": getattr(task, "decided_at", None),
        "display": display,
        "context": context,
        "risk_level": risk_level,
        "priority_score": priority_score,
        "priority_band": (
            "critical" if priority_score >= 80 else "high" if priority_score >= 60 else "normal"
        ),
        "priority_reasons": (
            (["高风险原因"] if risk_level == "high" else [])
            + (["事件重要度高"] if importance >= 0.7 else [])
            + (["已超过SLA"] if sla_factor > 1 else [])
        ),
        "review_state": (
            _review_queue_state(task, attempts, now)
            if object_type != "merge_review"
            else ("decided" if status == "decided" else "pending")
        ),
        "last_auto_review_status": latest.status if latest else None,
        "last_auto_review_at": latest.created_at if latest else None,
        "last_auto_review_confidence": latest.confidence if latest else None,
        "last_auto_review_reason": latest.reason if latest else None,
        "auto_review_attempt_count": len(attempts),
        "reviewer_type": (
            "agent"
            if getattr(task, "reviewer_id", None) == "agent:default_reviewer"
            else ("human" if getattr(task, "reviewer_id", None) else "none")
        ),
        "age_seconds": max(0, int((now - (task.created_at or now)).total_seconds())),
        "sla_seconds": 3600,
    }


@router.get("/api/v1/review-queue/overview", response_model=DataEnvelope)
def review_queue_overview(
    request: Request,
    _user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    now = datetime.now(timezone.utc)
    repository = request.app.state.repository
    tasks = repository.list_review_tasks(limit=500)
    merge_tasks = repository.list_merge_review_tasks(limit=500)
    cache: dict = {}
    items = [_review_queue_item(repository, task, now, cache) for task in tasks]
    items.extend(_review_queue_item(repository, task, now, cache) for task in merge_tasks)
    counts: dict[str, int] = {}
    for item in items:
        counts[item["review_state"]] = counts.get(item["review_state"], 0) + 1
    return envelope(
        {
            "counts": counts,
            "total": len(items),
            "oldest_pending_at": min(
                (
                    item["created_at"]
                    for item in items
                    if item["review_state"] in {"pending", "escalated_to_human"}
                ),
                default=None,
            ),
            "refreshed_at": now,
        },
        request.state.request_id,
    )


@router.get("/api/v1/review-queue/items", response_model=DataEnvelope)
def review_queue_items(
    request: Request,
    status_filter: Annotated[Optional[str], Query()] = None,
    object_type: Annotated[Optional[str], Query()] = None,
    risk_level: Annotated[Optional[str], Query(pattern="^(high|normal)$")] = None,
    sort: Annotated[str, Query(pattern="^(priority_desc|created_desc|sla_asc)$")] = "priority_desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    _user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    now = datetime.now(timezone.utc)
    repository = request.app.state.repository
    tasks = repository.list_review_tasks(limit=500)
    merge_tasks = repository.list_merge_review_tasks(limit=500)
    cache: dict = {}
    items = [_review_queue_item(repository, task, now, cache) for task in tasks]
    items.extend(_review_queue_item(repository, task, now, cache) for task in merge_tasks)
    if status_filter:
        items = [item for item in items if item["review_state"] == status_filter]
    if object_type:
        items = [item for item in items if item["object_type"] == object_type]
    if risk_level:
        items = [item for item in items if item["risk_level"] == risk_level]
    if sort == "created_desc":
        items.sort(
            key=lambda item: item.get("created_at")
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
    elif sort == "sla_asc":
        items.sort(key=lambda item: item.get("age_seconds", 0), reverse=True)
    else:
        items.sort(key=lambda item: item.get("priority_score", 0), reverse=True)
    items = items[:limit]
    return envelope(items, request.state.request_id)


@router.get("/api/v1/review-queue/{task_id}/timeline", response_model=DataEnvelope)
def review_queue_timeline(
    task_id: str,
    request: Request,
    _user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    task = request.app.state.repository.get_review_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="REVIEW_NOT_FOUND")
    attempts = request.app.state.repository.list_auto_review_attempts(task.id, limit=100)
    timeline = [
        {
            "type": "created",
            "at": task.created_at,
            "details": {"reason_code": task.reason_code},
        }
    ]
    timeline.extend(
        {"type": "auto_review", "at": item.created_at, "details": item.__dict__}
        for item in reversed(attempts)
    )
    if task.decided_at:
        timeline.append(
            {
                "type": "decided",
                "at": task.decided_at,
                "details": {
                    "decision": task.decision,
                    "reviewer_id": task.reviewer_id,
                    "comment": task.comment,
                },
            }
        )
    return envelope(timeline, request.state.request_id)


@router.post(
    "/api/v1/events/{event_id}/workflows",
    response_model=DataEnvelope,
    status_code=201,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def create_workflow(
    event_id: str,
    payload: WorkflowCreateRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    operation = f"workflow.create:{event_id}"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, payload
    )
    if replay:
        return replay
    if not request.app.state.repository.get_event(event_id):
        raise HTTPException(status_code=404, detail="EVENT_NOT_FOUND")
    service = WorkflowService(request.app.state.repository)
    run = service.create(event_id, payload.trigger_id, payload.as_of)
    if payload.execute:
        run = service.run(run.id)
    response = envelope(
        WorkflowResponse.model_validate(run, from_attributes=True), request.state.request_id
    )
    return _idempotency_finish(request, storage_key, request_hash, operation, run.id, response)


@router.post(
    "/api/v1/workflows/{workflow_id}/run",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def run_workflow(
    workflow_id: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    """Start a pending workflow (Admin/API click-through without a workflow worker)."""
    operation = f"workflow.run:{workflow_id}"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, {"workflow_id": workflow_id}
    )
    if replay:
        return replay
    run = request.app.state.repository.get_workflow_run(workflow_id)
    if not run:
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    if run.status != "pending":
        raise HTTPException(status_code=409, detail="WORKFLOW_NOT_RUNNABLE")
    updated = WorkflowService(request.app.state.repository).run(workflow_id)
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="workflow.run",
            object_type="workflow",
            object_id=workflow_id,
            request_id=request.state.request_id,
            details={"status": updated.status, "error_code": updated.error_code},
        )
    )
    response = envelope(
        WorkflowResponse.model_validate(updated, from_attributes=True),
        request.state.request_id,
    )
    return _idempotency_finish(request, storage_key, request_hash, operation, updated.id, response)


@router.get("/api/v1/workflows/{workflow_id}", response_model=DataEnvelope)
def get_workflow(
    workflow_id: str,
    request: Request,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    run = request.app.state.repository.get_workflow_run(workflow_id)
    if not run:
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    return envelope(
        WorkflowResponse.model_validate(run, from_attributes=True), request.state.request_id
    )


@router.get("/api/v1/workflows", response_model=DataEnvelope)
def list_workflows(
    request: Request,
    event_id: Optional[str] = None,
    status_filter: Annotated[
        Optional[str],
        Query(pattern="^(pending|running|succeeded|failed|waiting_review|cancelled)$"),
    ] = None,
    cursor: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    _user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"event_id", "status_filter", "cursor", "limit"})
    _validate_cursor(cursor)
    runs = request.app.state.repository.list_workflow_runs(
        event_id=event_id, status=status_filter, limit=limit + 1, cursor=cursor
    )
    values = [WorkflowResponse.model_validate(run, from_attributes=True) for run in runs]
    return _page_envelope(values, limit, lambda value: value.created_at, request.state.request_id)


@router.get("/api/v1/events/{event_id}/workflows", response_model=DataEnvelope)
def list_event_workflows(
    event_id: str,
    request: Request,
    cursor: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    _user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"cursor", "limit"})
    _validate_cursor(cursor)
    if not request.app.state.repository.get_event(event_id):
        raise HTTPException(status_code=404, detail="EVENT_NOT_FOUND")
    runs = request.app.state.repository.list_workflow_runs(
        event_id=event_id, limit=limit + 1, cursor=cursor
    )
    values = [WorkflowResponse.model_validate(run, from_attributes=True) for run in runs]
    return _page_envelope(values, limit, lambda value: value.created_at, request.state.request_id)


@router.get("/api/v1/workflows/{workflow_id}/budget", response_model=DataEnvelope)
def get_workflow_budget(
    workflow_id: str,
    request: Request,
    _user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    if not request.app.state.repository.get_workflow_run(workflow_id):
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    entries = request.app.state.repository.list_budget_ledger(workflow_id)
    return envelope(
        [BudgetLedgerEntryResponse.model_validate(e, from_attributes=True) for e in entries],
        request.state.request_id,
    )


@router.get("/api/v1/workflows/{workflow_id}/attempts", response_model=DataEnvelope)
def get_workflow_attempts(
    workflow_id: str,
    request: Request,
    _user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    if not request.app.state.repository.get_workflow_run(workflow_id):
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    attempts = request.app.state.repository.list_node_attempts(workflow_id)
    return envelope(
        [NodeAttemptResponse.model_validate(a, from_attributes=True) for a in attempts],
        request.state.request_id,
    )


@router.post(
    "/api/v1/research",
    response_model=DataEnvelope,
    status_code=201,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def create_research(
    payload: ResearchCreateRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    operation = "research.create"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, payload
    )
    if replay:
        return replay

    if payload.event_id and not request.app.state.repository.get_event(payload.event_id):
        raise HTTPException(status_code=404, detail="EVENT_NOT_FOUND")

    service = DynamicWorkflowService(
        request.app.state.repository,
        registry=AgentRegistry(request.app.state.repository),
        model_gateway=getattr(request.app.state, "model_gateway", None),
    )
    run, plan = service.create_plan(
        question=payload.question,
        as_of=payload.as_of,
        event_id=payload.event_id,
        budget_profile=payload.budget_profile,
        trigger_id=f"manual:{user.id}",
    )
    if payload.execute:
        plan = service.execute(plan.id)

    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="research.created",
            object_type="research_plan",
            object_id=plan.id,
            request_id=request.state.request_id,
            details={
                "workflow_id": run.id,
                "question": payload.question,
                "status": plan.status,
                "event_id": payload.event_id,
            },
            created_at=datetime.now(timezone.utc),
        )
    )
    response = envelope(
        ResearchPlanResponse.model_validate(plan, from_attributes=True),
        request.state.request_id,
    )
    return _idempotency_finish(request, storage_key, request_hash, operation, plan.id, response)


@router.post(
    "/api/v1/research/{plan_id}/execute",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def execute_research(
    plan_id: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    operation = f"research.execute:{plan_id}"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, {"plan_id": plan_id}
    )
    if replay:
        return replay

    plan = request.app.state.repository.get_research_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="RESEARCH_PLAN_NOT_FOUND")
    if plan.status not in {"ready", "pending", "waiting_review"}:
        raise HTTPException(status_code=409, detail="RESEARCH_PLAN_NOT_EXECUTABLE")

    service = DynamicWorkflowService(
        request.app.state.repository,
        registry=AgentRegistry(request.app.state.repository),
        model_gateway=getattr(request.app.state, "model_gateway", None),
    )
    plan = service.execute(plan_id)

    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="research.executed",
            object_type="research_plan",
            object_id=plan.id,
            request_id=request.state.request_id,
            details={"workflow_id": plan.workflow_id, "status": plan.status},
            created_at=datetime.now(timezone.utc),
        )
    )
    response = envelope(
        ResearchPlanResponse.model_validate(plan, from_attributes=True),
        request.state.request_id,
    )
    return _idempotency_finish(request, storage_key, request_hash, operation, plan.id, response)


@router.get("/api/v1/research/{plan_id}", response_model=DataEnvelope)
def get_research_plan(
    plan_id: str,
    request: Request,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    plan = request.app.state.repository.get_research_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="RESEARCH_PLAN_NOT_FOUND")
    return envelope(
        ResearchPlanResponse.model_validate(plan, from_attributes=True),
        request.state.request_id,
    )


@router.get("/api/v1/research", response_model=DataEnvelope)
def list_research_plans(
    request: Request,
    status_filter: Annotated[
        Optional[str],
        Query(
            pattern="^(pending|planning|ready|running|waiting_review|succeeded|failed|cancelled)$"
        ),
    ] = None,
    cursor: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"status_filter", "cursor", "limit"})
    _validate_cursor(cursor)
    plans = request.app.state.repository.list_research_plans(
        status=status_filter, limit=limit + 1, cursor=cursor
    )
    values = [ResearchPlanListResponse.model_validate(p, from_attributes=True) for p in plans]
    return _page_envelope(values, limit, lambda value: value.created_at, request.state.request_id)


@router.get("/api/v1/research/{plan_id}/tasks", response_model=DataEnvelope)
def list_research_tasks(
    plan_id: str,
    request: Request,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    plan = request.app.state.repository.get_research_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="RESEARCH_PLAN_NOT_FOUND")
    tasks = request.app.state.repository.list_research_tasks(plan_id)
    return envelope(
        [ResearchTaskResponse.model_validate(t, from_attributes=True) for t in tasks],
        request.state.request_id,
    )


@router.get("/api/v1/research/{plan_id}/blackboard", response_model=DataEnvelope)
def get_research_blackboard(
    plan_id: str,
    request: Request,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    plan = request.app.state.repository.get_research_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="RESEARCH_PLAN_NOT_FOUND")
    run = request.app.state.repository.get_workflow_run(plan.workflow_id)
    if not run:
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    blackboard = run.blackboard or {}
    return envelope(
        ResearchBlackboardResponse(
            workflow_id=run.id,
            research_plan=blackboard.get("research_plan", {}),
            task_outputs=blackboard.get("task_outputs", {}),
        ),
        request.state.request_id,
    )


def _build_admin_metrics(repository: Any) -> dict[str, Any]:
    """聚合运营与质量指标（FR-010 / NFR-007）。"""
    workflows = repository.list_workflow_runs(limit=100_000)
    workflow_by_status: dict[str, int] = {}
    for run in workflows:
        workflow_by_status[run.status] = workflow_by_status.get(run.status, 0) + 1
    terminal = workflow_by_status.get("succeeded", 0) + workflow_by_status.get("failed", 0)
    success_rate = workflow_by_status.get("succeeded", 0) / terminal if terminal else None

    models = repository.list_model_runs(limit=100_000)
    total_cost = sum(float(run.estimated_cost_usd or 0) for run in models)
    latencies = [run.latency_ms for run in models if run.latency_ms is not None]
    avg_latency = sum(latencies) / len(latencies) if latencies else None
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    last_24h = [run for run in models if run.created_at and run.created_at >= since_24h]
    last_24h_cost = sum(float(run.estimated_cost_usd or 0) for run in last_24h)

    sources = repository.list_sources()
    source_by_status: dict[str, int] = {}
    for source in sources:
        source_by_status[source.status] = source_by_status.get(source.status, 0) + 1
    open_quarantine = len(repository.list_quarantine_items(status="open", limit=100_000))

    reviews = repository.list_review_tasks()
    pending_reviews = sum(1 for task in reviews if task.status == "pending")
    decided_reviews = sum(1 for task in reviews if task.status != "pending")
    review_total = len(reviews)
    manual_review_rate = decided_reviews / review_total if review_total else 0.0

    outbox_pending = len(repository.list_pending_outbox(limit=100_000))
    outbox_dead = len(repository.list_outbox(dead_lettered=True, limit=100_000))

    users = repository.list_users()
    active_users = sum(1 for user in users if user.status == "active")

    total_claims = repository.count_claims()
    claims_with_evidence = repository.count_claims_with_evidence()
    citation_completeness = claims_with_evidence / total_claims if total_claims else None

    return {
        "workflows": {
            "total": len(workflows),
            "by_status": workflow_by_status,
            "success_rate": success_rate,
        },
        "models": {
            "total_runs": len(models),
            "failures": sum(1 for run in models if run.status != "success"),
            "total_cost_usd": round(total_cost, 6),
            "avg_latency_ms": avg_latency,
            "last_24h_runs": len(last_24h),
            "last_24h_cost_usd": round(last_24h_cost, 6),
        },
        "sources": {
            "total": len(sources),
            "by_status": source_by_status,
            "open_quarantine": open_quarantine,
        },
        "reviews": {
            "pending": pending_reviews,
            "decided": decided_reviews,
            "manual_review_rate": manual_review_rate,
        },
        "outbox": {
            "pending": outbox_pending,
            "dead_lettered": outbox_dead,
        },
        "users": {
            "total": len(users),
            "active": active_users,
        },
        "citations": {
            "completeness_rate": citation_completeness,
            "claims_with_evidence": claims_with_evidence,
            "total_claims": total_claims,
        },
    }


@router.get("/api/v1/admin/metrics", response_model=DataEnvelope)
def get_admin_metrics(
    request: Request,
    _user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    metrics = AdminMetricsResponse(**_build_admin_metrics(request.app.state.repository))
    return envelope(metrics.model_dump(), request.state.request_id)


@router.post(
    "/api/v1/workflows/{workflow_id}/resume",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def resume_workflow(
    workflow_id: str,
    payload: WorkflowResumeRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    operation = f"workflow.resume:{workflow_id}"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, payload
    )
    if replay:
        return replay
    run = request.app.state.repository.get_workflow_run(workflow_id)
    if not run:
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    if run.status not in {"waiting_review", "failed"}:
        raise HTTPException(status_code=409, detail="WORKFLOW_NOT_RESUMABLE")
    updated = WorkflowService(request.app.state.repository).resume(
        workflow_id,
        trigger=payload.trigger,
        resume_from=payload.resume_from,
        budget_adjust=payload.budget_adjust,
        force_fact_only=payload.force_fact_only,
        actor_id=user.id,
        reason=payload.reason,
    )
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="workflow.resume",
            object_type="workflow",
            object_id=workflow_id,
            request_id=request.state.request_id,
            details={
                "trigger": payload.trigger,
                "resume_from": payload.resume_from,
                "force_fact_only": payload.force_fact_only,
            },
            created_at=datetime.now(timezone.utc),
        )
    )
    response = envelope(
        WorkflowResponse.model_validate(updated, from_attributes=True), request.state.request_id
    )
    return _idempotency_finish(request, storage_key, request_hash, operation, updated.id, response)


@router.get("/api/v1/reviews/{task_id}", response_model=DataEnvelope)
def get_review(
    task_id: str,
    request: Request,
    _user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    task = request.app.state.repository.get_review_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="REVIEW_NOT_FOUND")
    return envelope(
        ReviewTaskResponse.model_validate(task, from_attributes=True), request.state.request_id
    )


def _event_type_registry_service(request: Request) -> EventTypeRegistryService:
    settings = getattr(request.app.state, "settings", None)
    threshold = getattr(settings, "candidate_type_promotion_threshold", 5)
    return EventTypeRegistryService(request.app.state.repository, promotion_threshold=threshold)


def _event_type_view(service: EventTypeRegistryService, entry) -> EventTypeRegistryResponse:
    return EventTypeRegistryResponse(
        type_label=entry.type_label,
        status=entry.status,
        event_count=entry.event_count,
        promotion_ready=service.is_promotion_ready(entry),
        decided_by=entry.decided_by,
        decided_at=entry.decided_at,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.get("/api/v1/event-types", response_model=DataEnvelope)
def list_event_types(
    request: Request,
    status_filter: Annotated[
        Optional[str], Query(pattern="^(candidate|accepted|rejected)$")
    ] = None,
    _user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"status_filter"})
    service = _event_type_registry_service(request)
    values = [_event_type_view(service, entry) for entry in service.list_entries(status_filter)]
    return envelope(values, request.state.request_id)


@router.post(
    "/api/v1/event-types/{type_label}/accept",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def accept_event_type(
    type_label: str,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    service = _event_type_registry_service(request)
    try:
        entry = service.accept(type_label, user.id)
    except EventTypeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="EVENT_TYPE_NOT_FOUND") from exc
    except EventTypeAlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail="EVENT_TYPE_ALREADY_DECIDED") from exc
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="event_type.promoted",
            object_type="event_type",
            object_id=type_label,
            request_id=request.state.request_id,
            details={"status": "accepted", "event_count": entry.event_count},
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(_event_type_view(service, entry), request.state.request_id)


@router.post(
    "/api/v1/event-types/{type_label}/reject",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def reject_event_type(
    type_label: str,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    service = _event_type_registry_service(request)
    try:
        entry = service.reject(type_label, user.id)
    except EventTypeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="EVENT_TYPE_NOT_FOUND") from exc
    except EventTypeAlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail="EVENT_TYPE_ALREADY_DECIDED") from exc
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="event_type.rejected",
            object_type="event_type",
            object_id=type_label,
            request_id=request.state.request_id,
            details={"status": "rejected", "event_count": entry.event_count},
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(_event_type_view(service, entry), request.state.request_id)


@router.get("/api/v1/merge-reviews", response_model=DataEnvelope)
def list_merge_reviews(
    request: Request,
    status_filter: Annotated[Optional[str], Query(pattern="^(open|decided)$")] = None,
    cursor: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    _user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"status_filter", "cursor", "limit"})
    _validate_cursor(cursor)
    tasks = request.app.state.repository.list_merge_review_tasks(
        status=status_filter, limit=limit + 1, cursor=cursor
    )
    values = [MergeReviewTaskResponse.model_validate(task, from_attributes=True) for task in tasks]
    return _page_envelope(values, limit, lambda value: value.created_at, request.state.request_id)


@router.get("/api/v1/merge-reviews/{task_id}", response_model=DataEnvelope)
def get_merge_review(
    task_id: str,
    request: Request,
    _user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    task = request.app.state.repository.get_merge_review_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="MERGE_REVIEW_NOT_FOUND")
    return envelope(
        MergeReviewTaskResponse.model_validate(task, from_attributes=True),
        request.state.request_id,
    )


@router.post(
    "/api/v1/merge-reviews/{task_id}/decision",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def decide_merge_review(
    task_id: str,
    payload: MergeReviewDecisionRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    operation = f"merge_review.decision:{task_id}"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, payload
    )
    if replay:
        return replay
    repository = request.app.state.repository
    task = repository.get_merge_review_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="MERGE_REVIEW_NOT_FOUND")
    if task.status != "open":
        raise HTTPException(status_code=409, detail="MERGE_REVIEW_ALREADY_DECIDED")
    if payload.decision not in {"merge", "new_event", "skip"}:
        raise HTTPException(status_code=409, detail="MERGE_REVIEW_DECISION_INVALID")

    updated = task.__class__(
        **{
            **task.__dict__,
            "status": "decided",
            "decision": payload.decision,
            "reviewer_id": user.id,
            "decided_at": datetime.now(timezone.utc),
        }
    )
    repository.update_merge_review_task(updated)
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="merge_review.decided",
            object_type="merge_review_task",
            object_id=task.id,
            request_id=request.state.request_id,
            details={
                "decision": payload.decision,
                "comment": payload.comment,
                "candidates": task.candidates,
            },
            created_at=datetime.now(timezone.utc),
        )
    )
    response = envelope(
        MergeReviewTaskResponse.model_validate(updated, from_attributes=True),
        request.state.request_id,
    )
    return _idempotency_finish(request, storage_key, request_hash, operation, updated.id, response)


@router.get("/api/v1/evidence/{evidence_id}", response_model=DataEnvelope)
def get_evidence(
    evidence_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    evidence = repository.get_evidence(evidence_id)
    if not evidence:
        raise HTTPException(status_code=404, detail="EVIDENCE_NOT_FOUND")
    document = repository.get_document(evidence.document_id)
    resolver = CitationResolver(repository)
    display_role = CitationResolver.citation_role_for_api(user.role)
    source = repository.get_source(document.source_id) if document is not None else None
    raw_content = document.content if document else None
    # 历史脏入库（脚本/导航残留）在展示前清洗，不改库
    cleaned_content = scrub_extracted_text(raw_content) if raw_content else None
    cleaned_excerpt = scrub_extracted_text(evidence.excerpt)
    if not cleaned_excerpt and cleaned_content:
        cleaned_excerpt = cleaned_content.splitlines()[0][:200]
    if not cleaned_excerpt:
        cleaned_excerpt = evidence.excerpt
    scope, document_content = resolver.authorized_document_content(
        cleaned_content,
        role=display_role,
        source_tier=document.source_tier if document else None,
        license=source.license if source else "inherit",
    )
    payload = {k: v for k, v in evidence.__dict__.items()}
    payload["excerpt"] = cleaned_excerpt
    return envelope(
        EvidenceResponse(
            **payload,
            document_title=document.title if document else None,
            document_url=document.canonical_url if document else None,
            document_content=document_content,
            display_scope=scope,
        ),
        request.state.request_id,
    )


@router.get(
    "/api/v1/conflicts/{conflict_id}",
    response_model=DataEnvelope,
    responses=openapi_error_responses(401, 404),
)
def get_conflict(
    conflict_id: str,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    conflict = repository.get_conflict(conflict_id)
    if not conflict:
        raise HTTPException(status_code=404, detail="CONFLICT_NOT_FOUND")
    return envelope(
        ConflictResponse.model_validate(conflict, from_attributes=True),
        request.state.request_id,
    )


@router.post(
    "/api/v1/reviews/{task_id}/decision",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def decide_review(
    task_id: str,
    payload: ReviewDecisionRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    operation = f"review.decision:{task_id}"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, payload
    )
    if replay:
        return replay
    repository = request.app.state.repository
    task = repository.get_review_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="REVIEW_NOT_FOUND")
    if task.status != "pending" or payload.decision not in task.allowed_decisions:
        raise HTTPException(status_code=409, detail="REVIEW_DECISION_INVALID")
    if task.object_type == "report":
        if payload.decision not in {"approve", "return", "reject"}:
            raise HTTPException(status_code=409, detail="REVIEW_DECISION_INVALID")
        card = repository.get_fact_card(task.object_id)
        if not card:
            raise HTTPException(status_code=409, detail="REVIEW_OBJECT_NOT_FOUND")
        status_by_decision = {
            "approve": "approved",
            "return": "needs_revision",
            "reject": "withdrawn",
        }
        try:
            updated = FactCardService(repository).transition(
                card, status_by_decision[payload.decision], f"review: {payload.comment}"
            )
        except ReportVersionConflict as exc:
            raise HTTPException(status_code=409, detail="REPORT_VERSION_CONFLICT") from exc
        response_payload = FactCardResponse.model_validate(updated, from_attributes=True)
    elif task.object_type == "workflow":
        workflows = WorkflowService(repository)
        run = repository.get_workflow_run(task.object_id)
        if not run:
            raise HTTPException(status_code=409, detail="REVIEW_OBJECT_NOT_FOUND")
        decision = payload.decision
        if decision == "return_for_supplement":
            decision = "return"
        if decision == "approve":
            updated_run = workflows.resume(
                run.id,
                trigger="budget_resume",
                resume_from=payload.resume_from or task.resume_from,
                budget_adjust=payload.budget_adjust or {"model_calls": 10, "tool_calls": 20},
                actor_id=user.id,
                reason=payload.comment,
            )
        elif decision == "downgrade_to_fact_card":
            updated_run = workflows.resume(
                run.id,
                trigger="downgrade_fact_only",
                force_fact_only=True,
                actor_id=user.id,
                reason=payload.comment,
            )
        elif decision == "return":
            updated_run = workflows.resume(
                run.id,
                trigger="company_returned",
                resume_from=payload.resume_from or task.resume_from or "company",
                actor_id=user.id,
                reason=payload.comment,
            )
        elif decision == "reject":
            updated_run = workflows.cancel(run.id, reason=payload.comment)
        else:
            raise HTTPException(status_code=409, detail="REVIEW_DECISION_INVALID")
        response_payload = WorkflowResponse.model_validate(updated_run, from_attributes=True)
    elif task.object_type == "claim_conflict":
        if payload.decision not in {"approve", "reject"}:
            raise HTTPException(status_code=409, detail="REVIEW_DECISION_INVALID")
        conflict = repository.get_conflict(task.object_id)
        if not conflict:
            raise HTTPException(status_code=409, detail="REVIEW_OBJECT_NOT_FOUND")
        claim_status = "verified" if payload.decision == "approve" else "rejected"
        for claim_id in conflict.claim_ids:
            claim = repository.get_claim(claim_id)
            if claim and claim.status == "conflicted":
                repository.update_claim(
                    claim.__class__(**{**claim.__dict__, "status": claim_status})
                )
        repository.update_conflict(
            conflict.__class__(
                **{
                    **conflict.__dict__,
                    "status": "resolved",
                    "resolution": payload.decision,
                    "version": conflict.version + 1,
                }
            )
        )
        response_payload = {"conflict_id": conflict.id, "resolution": payload.decision}
    else:
        raise HTTPException(status_code=409, detail="REVIEW_OBJECT_UNSUPPORTED")

    completed = task.__class__(
        **{
            **task.__dict__,
            "status": "decided",
            "decision": payload.decision,
            "reviewer_id": user.id,
            "comment": payload.comment,
            "decided_at": datetime.now(timezone.utc),
        }
    )
    repository.update_review_task(completed)
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="review.decided",
            object_type="review_task",
            object_id=task.id,
            request_id=request.state.request_id,
            details={
                "decision": payload.decision,
                "object_type": task.object_type,
                "object_id": task.object_id,
            },
            created_at=datetime.now(timezone.utc),
        )
    )
    response = envelope(response_payload, request.state.request_id)
    return _idempotency_finish(request, storage_key, request_hash, operation, task.id, response)


@router.post(
    "/api/v1/sources",
    response_model=DataEnvelope,
    status_code=201,
    responses=openapi_error_responses(400, 401, 409, 422),
)
def create_source(
    payload: SourceCreateRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    operation = "source.create"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, payload
    )
    if replay:
        return replay
    repository = request.app.state.repository
    if repository.get_source_by_code(payload.code):
        raise HTTPException(status_code=409, detail="SOURCE_CODE_EXISTS")
    source = Source(
        id=new_id("src"),
        code=payload.code,
        name=payload.name,
        trust_tier=payload.trust_tier,
        feed_url=payload.feed_url,
        allowed_domains=payload.allowed_domains,
        adapter_type=payload.adapter_type,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        crawl_interval_seconds=payload.crawl_interval_seconds,
        license=payload.license,
        extra_config=payload.extra_config,
    )
    with repository.transaction() as transaction:
        transaction.save_source(source)
        transaction.save_audit_log(
            AuditLog(
                id=new_id("aud"),
                actor_id=user.id,
                action="source.create",
                object_type="source",
                object_id=source.id,
                request_id=request.state.request_id,
                details={"code": source.code},
                created_at=datetime.now(timezone.utc),
            )
        )
    response = envelope(SourceResponse(**source.__dict__), request.state.request_id)
    return _idempotency_finish(request, storage_key, request_hash, operation, source.id, response)


@router.post(
    "/api/v1/sources/{source_id}/sync",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
async def sync_source(
    source_id: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    operation = f"source.sync:{source_id}"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, {}
    )
    if replay:
        return replay
    source = request.app.state.repository.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    result = await request.app.state.rss_sync.sync(
        source,
        trigger="manual",
        request_id=request.state.request_id,
    )
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="source.sync",
            object_type="source",
            object_id=source.id,
            request_id=request.state.request_id,
            details=result,
            created_at=datetime.now(timezone.utc),
        )
    )
    response = envelope(result, request.state.request_id)
    return _idempotency_finish(request, storage_key, request_hash, operation, source.id, response)


@router.post(
    "/api/v1/sources/sync-all",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 409, 422),
)
async def sync_all_sources(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    operation = "source.sync_all"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, {}
    )
    if replay:
        return replay
    now = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    for source in request.app.state.repository.list_sources():
        if source.status != "active":
            continue
        if source.next_retry_at and source.next_retry_at > now:
            continue
        # Re-load each time so previous sync status updates are visible.
        current = request.app.state.repository.get_source(source.id) or source
        result = await request.app.state.rss_sync.sync(
            current,
            trigger="sync_all",
            request_id=request.state.request_id,
        )
        results.append({"source_id": source.id, "code": source.code, **result})
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="source.sync_all",
            object_type="source",
            object_id=None,
            request_id=request.state.request_id,
            details={"count": len(results)},
            created_at=datetime.now(timezone.utc),
        )
    )
    response = envelope(
        {"synced": len(results), "results": results},
        request.state.request_id,
    )
    return _idempotency_finish(request, storage_key, request_hash, operation, "sources", response)


@router.get("/api/v1/sources/{source_id}/runs", response_model=DataEnvelope)
def list_source_runs(
    source_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Optional[str] = None,
    user: User = Depends(require_roles("admin", "researcher", "reviewer", "publisher")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"cursor", "limit"})
    _validate_cursor(cursor)
    source = request.app.state.repository.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    runs = [
        IngestRunResponse(**run.__dict__)
        for run in request.app.state.repository.list_ingest_runs(
            source_id, limit=limit + 1, cursor=cursor
        )
    ]
    return _page_envelope(runs, limit, lambda value: value.started_at, request.state.request_id)


@router.get(
    "/api/v1/sources/{source_id}/health",
    response_model=DataEnvelope,
    responses=openapi_error_responses(401, 404),
)
def get_source_health(
    source_id: str,
    request: Request,
    user: User = Depends(require_roles("admin", "researcher", "reviewer", "publisher")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    source = repository.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")

    runs = repository.list_ingest_runs(source_id, limit=10)
    recent_runs = [IngestRunResponse(**run.__dict__) for run in runs]
    last_run = recent_runs[0] if recent_runs else None

    if source.status == "disabled":
        health = "disabled"
    elif source.status == "degraded" or source.consecutive_failures > 0:
        health = "degraded"
    else:
        health = "healthy"

    return envelope(
        SourceHealthResponse(
            source=SourceResponse(**source.__dict__),
            health=health,
            consecutive_failures=source.consecutive_failures,
            last_success_at=source.last_success_at,
            last_run=last_run,
            recent_runs=recent_runs,
        ),
        request.state.request_id,
    )


@router.patch(
    "/api/v1/sources/{source_id}",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def update_source(
    source_id: str,
    payload: SourceUpdateRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    operation = f"source.update:{source_id}"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, payload.model_dump(exclude_unset=True)
    )
    if replay:
        return replay
    repository = request.app.state.repository
    source = repository.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="SOURCE_UPDATE_EMPTY")
    data = {**source.__dict__, **updates}
    if updates.get("status") == "active":
        data["consecutive_failures"] = 0
        data["last_error_code"] = None
        data["next_retry_at"] = None
    updated = Source(**data)
    repository.update_source(updated)
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="source.update",
            object_type="source",
            object_id=source.id,
            request_id=request.state.request_id,
            details=updates,
            created_at=datetime.now(timezone.utc),
        )
    )
    response = envelope(SourceResponse(**updated.__dict__), request.state.request_id)
    return _idempotency_finish(request, storage_key, request_hash, operation, updated.id, response)


@router.post(
    "/api/v1/sources/seed",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 409, 422),
)
def seed_default_sources(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    operation = "source.seed"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, {}
    )
    if replay:
        return replay
    repository = request.app.state.repository
    inserted = seed_sources(repository, skip_existing=True)
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="source.seed",
            object_type="source",
            object_id=None,
            details={"inserted": inserted},
            request_id=request.state.request_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    response = envelope({"inserted": inserted}, request.state.request_id)
    return _idempotency_finish(request, storage_key, request_hash, operation, "sources", response)


def _llm_http_error(exc: LlmConfigError) -> HTTPException:
    code = str(exc)
    status_code = {
        "LLM_PROVIDER_NOT_FOUND": 404,
        "LLM_PROVIDER_CODE_EXISTS": 409,
        "LLM_API_KEY_REQUIRED": 400,
        "LLM_PROTOCOL_UNSUPPORTED": 400,
        "LLM_AGENT_KEY_INVALID": 400,
        "LLM_BASE_URL_REQUIRED": 400,
        "LLM_API_KEY_MISSING": 400,
        "LLM_DETERMINISTIC_HAS_NO_KEY": 400,
    }.get(code, 400)
    return HTTPException(status_code=status_code, detail=code)


@router.get("/api/v1/admin/review-policy", response_model=DataEnvelope)
def get_review_policy(
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    settings = request.app.state.settings
    policy = request.app.state.repository.get_review_policy()
    source = "environment" if policy.updated_at is None else "database"
    return envelope(
        ReviewPolicyResponse(
            id=policy.id,
            mode="human" if settings.auto_review_disabled else policy.mode,
            min_confidence=policy.min_confidence,
            source=source,
            updated_by=policy.updated_by,
            updated_at=policy.updated_at,
            emergency_disabled=settings.auto_review_disabled,
        ),
        request.state.request_id,
    )


@router.patch("/api/v1/admin/review-policy", response_model=DataEnvelope)
def update_review_policy(
    payload: ReviewPolicyUpdateRequest,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    if request.app.state.settings.auto_review_disabled and payload.mode == "agent":
        raise HTTPException(status_code=409, detail="AUTO_REVIEW_EMERGENCY_DISABLED")
    current = request.app.state.repository.get_review_policy()
    updated = ReviewPolicy(
        id=current.id,
        mode=payload.mode,
        min_confidence=current.min_confidence,
        updated_by=user.id,
        updated_at=datetime.now(timezone.utc),
    )
    request.app.state.repository.save_review_policy(updated)
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="review.policy.updated",
            object_type="review_policy",
            object_id=updated.id,
            request_id=request.state.request_id,
            details={"mode": updated.mode, "previous_mode": current.mode},
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(
        ReviewPolicyResponse(
            id=updated.id,
            mode=updated.mode,
            min_confidence=updated.min_confidence,
            source="database",
            updated_by=updated.updated_by,
            updated_at=updated.updated_at,
            emergency_disabled=request.app.state.settings.auto_review_disabled,
        ),
        request.state.request_id,
    )


@router.get("/api/v1/llm/presets", response_model=DataEnvelope)
def get_llm_presets(
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    return envelope(list_presets(), request.state.request_id)


@router.get("/api/v1/llm/providers", response_model=DataEnvelope)
def list_llm_providers(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Optional[str] = None,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    _validate_query(request, {"cursor", "limit"})
    _validate_cursor(cursor)
    secrets = SecretBox.from_settings()
    providers = request.app.state.repository.list_llm_providers(limit=limit + 1, cursor=cursor)
    page, next_cursor = page_items(providers, limit, lambda value: value.created_at)
    response = envelope(
        [public_provider_view(item, secrets) for item in page],
        request.state.request_id,
    )
    response.meta["next_cursor"] = next_cursor
    return response


@router.post(
    "/api/v1/llm/providers",
    response_model=DataEnvelope,
    status_code=201,
    responses=openapi_error_responses(400, 401, 409, 422),
)
def create_llm_provider(
    payload: LlmProviderCreateRequest,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    secrets = SecretBox.from_settings()
    try:
        config = create_provider(
            request.app.state.repository,
            secrets,
            code=payload.code,
            display_name=payload.display_name,
            protocol=payload.protocol,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key,
            is_default=payload.is_default,
            timeout_seconds=payload.timeout_seconds,
            max_tokens=payload.max_tokens,
            temperature=payload.temperature,
            extra_config=payload.extra_config,
            status=payload.status,
        )
    except LlmConfigError as exc:
        raise _llm_http_error(exc) from exc
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="llm.provider.create",
            object_type="llm_provider",
            object_id=config.id,
            details={
                "code": config.code,
                "protocol": config.protocol,
                "api_key_configured": bool(config.api_key_encrypted),
            },
            request_id=request.state.request_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(public_provider_view(config, secrets), request.state.request_id)


@router.patch(
    "/api/v1/llm/providers/{provider_id}",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 422),
)
def patch_llm_provider(
    provider_id: str,
    payload: LlmProviderUpdateRequest,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    secrets = SecretBox.from_settings()
    try:
        config = update_provider(
            request.app.state.repository,
            secrets,
            provider_id,
            display_name=payload.display_name,
            base_url=payload.base_url,
            model=payload.model,
            status=payload.status,
            is_default=payload.is_default,
            timeout_seconds=payload.timeout_seconds,
            max_tokens=payload.max_tokens,
            temperature=payload.temperature,
            extra_config=payload.extra_config,
        )
    except LlmConfigError as exc:
        raise _llm_http_error(exc) from exc
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="llm.provider.update",
            object_type="llm_provider",
            object_id=config.id,
            details={"fields": sorted(payload.model_dump(exclude_none=True).keys())},
            request_id=request.state.request_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(public_provider_view(config, secrets), request.state.request_id)


@router.post(
    "/api/v1/llm/providers/{provider_id}/rotate-key",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 422),
)
def rotate_llm_provider_key(
    provider_id: str,
    payload: LlmProviderRotateKeyRequest,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    secrets = SecretBox.from_settings()
    try:
        config = rotate_provider_key(
            request.app.state.repository,
            secrets,
            provider_id,
            api_key=payload.api_key,
        )
    except LlmConfigError as exc:
        raise _llm_http_error(exc) from exc
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="llm.provider.rotate_key",
            object_type="llm_provider",
            object_id=config.id,
            details={"via": "rotate-key", "code": config.code},
            request_id=request.state.request_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(public_provider_view(config, secrets), request.state.request_id)


@router.delete(
    "/api/v1/llm/providers/{provider_id}",
    response_model=DataEnvelope,
    responses=openapi_error_responses(401, 404),
)
def delete_llm_provider(
    provider_id: str,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    existing = repository.get_llm_provider(provider_id)
    if not existing:
        raise HTTPException(status_code=404, detail="LLM_PROVIDER_NOT_FOUND")
    repository.delete_llm_provider(provider_id)
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="llm.provider.delete",
            object_type="llm_provider",
            object_id=provider_id,
            details={"code": existing.code},
            request_id=request.state.request_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope({"deleted": True, "id": provider_id}, request.state.request_id)


@router.post(
    "/api/v1/llm/providers/{provider_id}/test",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404),
)
def probe_llm_provider(
    provider_id: str,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    secrets = SecretBox.from_settings()
    try:
        result = test_provider(request.app.state.repository, secrets, provider_id)
    except LlmConfigError as exc:
        raise _llm_http_error(exc) from exc
    return envelope(result, request.state.request_id)


@router.get("/api/v1/llm/bindings", response_model=DataEnvelope)
def list_llm_bindings(
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    items = [
        {
            "agent_key": item.agent_key,
            "provider_id": item.provider_id,
            "model_override": item.model_override,
            "updated_at": item.updated_at,
        }
        for item in request.app.state.repository.list_llm_agent_bindings()
    ]
    return envelope(items, request.state.request_id)


@router.put(
    "/api/v1/llm/bindings",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 422),
)
def put_llm_binding(
    payload: LlmAgentBindingRequest,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    try:
        binding = upsert_binding(
            request.app.state.repository,
            agent_key=payload.agent_key,
            provider_id=payload.provider_id,
            model_override=payload.model_override,
        )
    except LlmConfigError as exc:
        raise _llm_http_error(exc) from exc
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="llm.binding.upsert",
            object_type="llm_agent_binding",
            object_id=binding.agent_key,
            details={
                "provider_id": binding.provider_id,
                "model_override": binding.model_override,
            },
            request_id=request.state.request_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(
        {
            "agent_key": binding.agent_key,
            "provider_id": binding.provider_id,
            "model_override": binding.model_override,
            "updated_at": binding.updated_at,
        },
        request.state.request_id,
    )


@router.put(
    "/api/v1/llm/bindings/bulk",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 422),
)
def put_llm_bindings_bulk(
    payload: LlmAgentBindingBulkRequest,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    try:
        bindings = bind_all_agents(
            request.app.state.repository,
            provider_id=payload.provider_id,
            model_override=payload.model_override,
        )
    except LlmConfigError as exc:
        raise _llm_http_error(exc) from exc
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="llm.binding.bulk",
            object_type="llm_agent_binding",
            object_id=None,
            details={
                "provider_id": payload.provider_id,
                "model_override": payload.model_override,
                "agent_keys": [item.agent_key for item in bindings],
                "count": len(bindings),
            },
            request_id=request.state.request_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(
        [
            {
                "agent_key": item.agent_key,
                "provider_id": item.provider_id,
                "model_override": item.model_override,
                "updated_at": item.updated_at,
            }
            for item in bindings
        ],
        request.state.request_id,
    )


def envelope(data: object, request_id: str, *, next_cursor: Optional[str] = None) -> DataEnvelope:
    meta: dict[str, Any] = {"request_id": request_id, "schema_version": "1.0"}
    if next_cursor is not None:
        meta["next_cursor"] = next_cursor
    return DataEnvelope(data=data, meta=meta)


@router.get("/health/live")
@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(request: Request):
    repository = request.app.state.repository
    try:
        if hasattr(repository, "engine"):
            with repository.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "dependency": "database"},
        )
    return {"status": "ready"}


@router.post(
    "/api/v1/documents/ingest",
    response_model=DataEnvelope,
    status_code=status.HTTP_201_CREATED,
    responses=openapi_error_responses(400, 401, 409, 422),
)
def ingest_document(
    payload: IngestDocumentRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("researcher", "admin")),  # noqa: B008
) -> DataEnvelope:
    request_id = request.state.request_id
    operation = "document.ingest"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, payload
    )
    if replay:
        return replay
    try:
        result = request.app.state.pipeline.process(
            idempotency_key=f"pipeline:{storage_key}" if storage_key else None,
            **payload.model_dump(),
        )
    except ValueError as exc:
        if str(exc) in {"DOCUMENT_CONFLICT", "IDEMPOTENCY_CONFLICT"}:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    response = PipelineResponse(
        status=result.status,
        document_id=result.document.id,
        event_id=result.event.id,
        evidence_id=result.evidence.id,
        claim_id=result.claim.id,
        claim_status=result.claim.status,
        fact_card_id=result.fact_card.id,
        report_status=result.fact_card.status,
    )
    response_envelope = envelope(response, request_id)
    return _idempotency_finish(
        request,
        storage_key,
        request_hash,
        operation,
        result.document.id,
        response_envelope,
    )


@router.delete(
    "/api/v1/documents/{document_id}",
    response_model=DataEnvelope,
    responses=openapi_error_responses(401, 404, 409),
)
def soft_delete_document(
    document_id: str,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    """Admin soft-delete; blocked by retention_hold (409). Does not alter ingest path."""
    repository = request.app.state.repository
    existing = repository.get_document(document_id, include_deleted=True)
    if existing is None:
        raise HTTPException(status_code=404, detail="DOCUMENT_NOT_FOUND")
    try:
        deleted = repository.soft_delete_document(document_id)
    except RetentionHoldError as exc:
        raise HTTPException(status_code=409, detail="RETENTION_HOLD") from exc
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="document.soft_delete",
            object_type="document",
            object_id=document_id,
            details={
                "source_id": deleted.source_id,
                "deleted_at": deleted.deleted_at.isoformat() if deleted.deleted_at else None,
            },
            request_id=request.state.request_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(
        {
            "deleted": True,
            "id": deleted.id,
            "deleted_at": deleted.deleted_at,
        },
        request.state.request_id,
    )


@router.patch(
    "/api/v1/documents/{document_id}",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 422),
)
def patch_document(
    document_id: str,
    payload: DocumentUpdateRequest,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    """Admin update for retention_hold (legal hold). Soft-deleted docs remain patchable."""
    if payload.retention_hold is None:
        raise HTTPException(status_code=400, detail="DOCUMENT_UPDATE_EMPTY")
    repository = request.app.state.repository
    existing = repository.get_document(document_id, include_deleted=True)
    if existing is None:
        raise HTTPException(status_code=404, detail="DOCUMENT_NOT_FOUND")
    updated = repository.set_document_retention_hold(document_id, payload.retention_hold)
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="document.retention_hold",
            object_type="document",
            object_id=document_id,
            details={
                "retention_hold": updated.retention_hold,
                "previous_retention_hold": existing.retention_hold,
            },
            request_id=request.state.request_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(
        {
            "id": updated.id,
            "retention_hold": updated.retention_hold,
            "deleted_at": updated.deleted_at,
        },
        request.state.request_id,
    )


@router.post(
    "/api/v1/documents/{document_id}/purge",
    response_model=DataEnvelope,
    responses=openapi_error_responses(401, 404, 409),
)
def purge_document(
    document_id: str,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    """Destroy document body and evidence after soft-delete; custom retention window applies."""
    repository = request.app.state.repository
    existing = repository.get_document(document_id, include_deleted=True)
    if existing is None:
        raise HTTPException(status_code=404, detail="DOCUMENT_NOT_FOUND")
    settings = getattr(request.app.state, "settings", None)
    min_soft_delete_age_seconds = (
        settings.document_purge_min_age_seconds if settings is not None else 0
    )
    try:
        purged = repository.purge_document(
            document_id,
            min_soft_delete_age_seconds=min_soft_delete_age_seconds,
        )
    except RetentionHoldError as exc:
        raise HTTPException(status_code=409, detail="RETENTION_HOLD") from exc
    except DocumentNotSoftDeletedError as exc:
        raise HTTPException(status_code=409, detail="DOCUMENT_NOT_SOFT_DELETED") from exc
    except PurgeRetentionWindowError as exc:
        raise HTTPException(status_code=409, detail="PURGE_RETENTION_WINDOW") from exc
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="document.purge",
            object_type="document",
            object_id=document_id,
            details={
                "source_id": purged.source_id,
                "purged_at": purged.purged_at.isoformat() if purged.purged_at else None,
            },
            request_id=request.state.request_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    return envelope(
        {
            "purged": True,
            "id": purged.id,
            "purged_at": purged.purged_at,
            "deleted_at": purged.deleted_at,
        },
        request.state.request_id,
    )


@router.get("/api/v1/events", response_model=DataEnvelope)
def list_events(
    request: Request,
    cursor: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"cursor", "limit"})
    _validate_cursor(cursor)
    values = [
        EventResponse.model_validate(value, from_attributes=True)
        for value in request.app.state.repository.list_events(limit=limit + 1, cursor=cursor)
    ]
    return _page_envelope(values, limit, lambda value: value.occurred_at, request.state.request_id)


@router.get("/api/v1/ood/observations", response_model=DataEnvelope)
def list_ood_observations(
    request: Request,
    status: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"status", "limit"})
    values = [
        OODObservationResponse.model_validate(item, from_attributes=True)
        for item in request.app.state.repository.list_ood_observations(status=status, limit=limit)
    ]
    return envelope(values, request.state.request_id)


@router.get("/api/v1/ood/observations/{observation_id}", response_model=DataEnvelope)
def get_ood_observation(
    observation_id: str,
    request: Request,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, set())
    value = request.app.state.repository.get_ood_observation(observation_id)
    if value is None:
        raise HTTPException(status_code=404, detail="OOD_OBSERVATION_NOT_FOUND")
    return envelope(
        OODObservationResponse.model_validate(value, from_attributes=True),
        request.state.request_id,
    )


@router.get("/api/v1/ood/clusters", response_model=DataEnvelope)
def list_ood_clusters(
    request: Request,
    status: Optional[str] = None,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"status"})
    values = [
        OODClusterResponse.model_validate(item, from_attributes=True)
        for item in request.app.state.repository.list_ood_clusters(status=status)
    ]
    return envelope(values, request.state.request_id)


@router.post("/api/v1/ood/clusters/{cluster_id}/cluster", response_model=DataEnvelope)
def run_ood_clustering(
    cluster_id: str,
    request: Request,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, set())
    cluster = request.app.state.repository.get_ood_cluster(cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="OOD_CLUSTER_NOT_FOUND")
    proposal = OODLearningService(request.app.state.repository).propose_type(cluster)
    return envelope(
        EventTypeProposalResponse.model_validate(proposal, from_attributes=True),
        request.state.request_id,
    )


@router.get("/api/v1/ood/proposals", response_model=DataEnvelope)
def list_ood_proposals(
    request: Request,
    status: Optional[str] = None,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"status"})
    values = [
        EventTypeProposalResponse.model_validate(item, from_attributes=True)
        for item in request.app.state.repository.list_event_type_proposals(status=status)
    ]
    return envelope(values, request.state.request_id)


@router.post("/api/v1/ood/proposals/{proposal_id}/build-pack", response_model=DataEnvelope)
def build_ood_pack(
    proposal_id: str,
    request: Request,
    _user: User = Depends(require_roles("admin", "researcher")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, set())
    repository = request.app.state.repository
    proposal = repository.get_event_type_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="OOD_PROPOSAL_NOT_FOUND")
    pack = OODLearningService(repository).build_candidate_pack(proposal)
    return envelope(
        {"pack_id": pack.manifest.pack_id, "version": pack.manifest.version, "status": pack.status},
        request.state.request_id,
    )


@router.post(
    "/api/v1/capability-packs/{pack_id}/versions/{version}/evaluate",
    response_model=DataEnvelope,
)
def evaluate_capability_pack(
    pack_id: str,
    version: str,
    request: Request,
    _user: User = Depends(require_roles("admin", "researcher")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, set())
    service = OODLearningService(request.app.state.repository)
    pack = service.registry.get(pack_id, version)
    if pack is None:
        raise HTTPException(status_code=404, detail="CAPABILITY_PACK_NOT_FOUND")
    result = service.evaluate_pack(pack)
    return envelope(
        CapabilityEvaluationResponse.model_validate(result, from_attributes=True),
        request.state.request_id,
    )


@router.get("/api/v1/capability-evaluations", response_model=DataEnvelope)
def list_capability_evaluations(
    request: Request,
    pack_id: Optional[str] = None,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"pack_id"})
    values = [
        CapabilityEvaluationResponse.model_validate(item, from_attributes=True)
        for item in request.app.state.repository.list_capability_evaluations(pack_id=pack_id)
    ]
    return envelope(values, request.state.request_id)


@router.get("/api/v1/capability-packs", response_model=DataEnvelope)
def list_capability_packs(
    request: Request,
    pack_status: Optional[str] = None,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"pack_status"})
    packs = OODLearningService(request.app.state.repository).registry.list(status=pack_status)
    return envelope(
        [
            {
                "pack_id": pack.manifest.pack_id,
                "version": pack.manifest.version,
                "status": pack.manifest.status,
                "display_name": pack.manifest.display_name,
                "event_types": pack.manifest.event_types,
                "required_capabilities": pack.manifest.required_capabilities,
            }
            for pack in packs
        ],
        request.state.request_id,
    )


@router.post(
    "/api/v1/capability-packs/{pack_id}/versions/{version}/transition",
    response_model=DataEnvelope,
)
def transition_capability_pack(
    pack_id: str,
    version: str,
    request: Request,
    payload: dict[str, Any],
    _user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, set())
    status_value = str(payload.get("status") or "")
    try:
        pack = OODLearningService(request.app.state.repository).registry.transition(
            pack_id, version, status_value
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return envelope(
        {
            "pack_id": pack.manifest.pack_id,
            "version": pack.manifest.version,
            "status": pack.manifest.status,
        },
        request.state.request_id,
    )


@router.post("/api/v1/reprocessing/jobs", response_model=DataEnvelope)
def create_reprocessing_job(
    request: Request,
    payload: dict[str, Any],
    _user: User = Depends(require_roles("admin", "researcher")),  # noqa: B008
) -> DataEnvelope:
    target_pack_id = str(payload.get("target_pack_id") or "")
    event_ids = payload.get("event_ids") or []
    if not target_pack_id or not isinstance(event_ids, list):
        raise HTTPException(status_code=422, detail="INVALID_REPROCESSING_REQUEST")
    job = OODLearningService(request.app.state.repository).create_reprocessing_job(
        target_pack_id=target_pack_id,
        event_ids=[str(item) for item in event_ids],
        source_pack_id=payload.get("source_pack_id"),
    )
    return envelope(
        ReprocessingJobResponse.model_validate(job, from_attributes=True),
        request.state.request_id,
    )


@router.get("/api/v1/reprocessing/jobs", response_model=DataEnvelope)
def list_reprocessing_jobs(
    request: Request,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, set())
    values = [
        ReprocessingJobResponse.model_validate(item, from_attributes=True)
        for item in request.app.state.repository.list_reprocessing_jobs()
    ]
    return envelope(values, request.state.request_id)


@router.get("/api/v1/events/{event_id}", response_model=DataEnvelope)
def get_event(
    event_id: str,
    request: Request,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    event = repository.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="EVENT_NOT_FOUND")
    claims = [
        ClaimResponse.model_validate(value, from_attributes=True)
        for value in repository.get_claims_for_event(event_id)
    ]
    card = repository.get_fact_card_for_event(event_id)
    response = EventDetailResponse(
        **EventResponse.model_validate(event, from_attributes=True).model_dump(),
        claims=claims,
        fact_card_id=card.id if card else None,
    )
    return envelope(response, request.state.request_id)


@router.get("/api/v1/reports/{report_id}", response_model=DataEnvelope)
def get_report(
    report_id: str,
    request: Request,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    card = request.app.state.repository.get_fact_card(report_id)
    if not card:
        raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND")
    response = FactCardResponse.model_validate(card, from_attributes=True)
    return envelope(response, request.state.request_id)


@router.get("/api/v1/reports", response_model=DataEnvelope)
def list_reports(
    request: Request,
    event_id: Optional[str] = None,
    status_filter: Annotated[
        Optional[str],
        Query(
            pattern=(
                "^(draft|needs_review|review_required|approved|published|needs_revision|withdrawn)$"
            )
        ),
    ] = None,
    cursor: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    _user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"event_id", "status_filter", "cursor", "limit"})
    _validate_cursor(cursor)
    cards = request.app.state.repository.list_fact_cards(
        event_id=event_id,
        status=status_filter,
        limit=limit + 1,
        cursor=cursor,
    )
    response = [FactCardResponse.model_validate(card, from_attributes=True) for card in cards]
    return _page_envelope(response, limit, lambda value: value.as_of, request.state.request_id)


@router.get("/api/v1/reports/{report_id}/diff/{other_report_id}", response_model=DataEnvelope)
def diff_reports(
    report_id: str,
    other_report_id: str,
    request: Request,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    older = repository.get_fact_card(report_id)
    newer = repository.get_fact_card(other_report_id)
    if not older or not newer or older.event_id != newer.event_id:
        raise HTTPException(status_code=404, detail="REPORT_VERSION_NOT_FOUND")
    changes = {
        field: {"from": getattr(older, field), "to": getattr(newer, field)}
        for field in (
            "version",
            "status",
            "title",
            "summary",
            "claim_ids",
            "as_of",
            "content",
            "provenance",
        )
        if getattr(older, field) != getattr(newer, field)
    }
    return envelope(
        {"from_report_id": older.id, "to_report_id": newer.id, "changes": changes},
        request.state.request_id,
    )


@router.get("/api/v1/events/{event_id}/reports", response_model=DataEnvelope)
def list_event_reports(
    event_id: str,
    request: Request,
    cursor: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    _user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"cursor", "limit"})
    _validate_cursor(cursor)
    cards = request.app.state.repository.list_fact_cards(
        event_id=event_id, limit=limit + 1, cursor=cursor
    )
    if not cards:
        raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND")
    response = [FactCardResponse.model_validate(card, from_attributes=True) for card in cards]
    return _page_envelope(response, limit, lambda value: value.as_of, request.state.request_id)


@router.post(
    "/api/v1/events/{event_id}/impact-analysis",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409),
)
def generate_event_impact_analysis(
    event_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    event = repository.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="EVENT_NOT_FOUND")
    try:
        analysis = ImpactAnalysisService(repository).generate(event_id, actor=user.id)
    except ReportVersionConflict as exc:
        raise HTTPException(status_code=409, detail="IMPACT_ANALYSIS_VERSION_CONFLICT") from exc
    return DataEnvelope(
        data=ImpactAnalysisResponse.model_validate(analysis, from_attributes=True),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/events/{event_id}/impact-analysis", response_model=DataEnvelope)
def get_event_impact_analysis(
    event_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope | JSONResponse:
    repository = request.app.state.repository
    event = repository.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="EVENT_NOT_FOUND")
    analysis = repository.get_latest_impact_analysis_for_event(event_id)
    if analysis is not None:
        return DataEnvelope(
            data=ImpactAnalysisResponse.model_validate(analysis, from_attributes=True),
            meta={"request_id": request.state.request_id, "schema_version": "1.0"},
        )
    if _has_pending_impact_analysis(repository, event_id):
        return JSONResponse(
            status_code=202,
            content={
                "data": {"status": "pending"},
                "meta": {"request_id": request.state.request_id, "schema_version": "1.0"},
            },
        )
    raise HTTPException(status_code=404, detail="IMPACT_ANALYSIS_NOT_FOUND")


def _has_pending_impact_analysis(repository, event_id: str) -> bool:
    list_pending = getattr(repository, "list_pending_outbox_by_event_type", None)
    if not callable(list_pending):
        return False
    pending = list_pending("impact_analysis.requested.v1", limit=10)
    return any(msg.payload.get("event_id") == event_id for msg in pending)


@router.get("/api/v1/events/{event_id}/impact-analysis/versions", response_model=DataEnvelope)
def list_event_impact_analysis_versions(
    event_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    event = repository.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="EVENT_NOT_FOUND")
    analyses = repository.list_impact_analyses_for_event(event_id, limit=limit)
    response = [ImpactAnalysisResponse.model_validate(a, from_attributes=True) for a in analyses]
    return DataEnvelope(
        data=response,
        meta={
            "request_id": request.state.request_id,
            "schema_version": "1.0",
            "count": len(response),
        },
    )


@router.get("/api/v1/impact-analyses/{impact_analysis_id}", response_model=DataEnvelope)
def get_impact_analysis(
    impact_analysis_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    analysis = repository.get_impact_analysis(impact_analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="IMPACT_ANALYSIS_NOT_FOUND")
    return DataEnvelope(
        data=ImpactAnalysisResponse.model_validate(analysis, from_attributes=True),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.post(
    "/api/v1/impact-analyses/{impact_analysis_id}/transition",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 403, 404, 409),
)
def transition_impact_analysis(
    impact_analysis_id: str,
    payload: ImpactAnalysisTransitionRequest,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    analysis = repository.get_impact_analysis(impact_analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="IMPACT_ANALYSIS_NOT_FOUND")
    allowed = {
        "draft": {"needs_review", "rejected"},
        "needs_review": {"approved", "draft", "rejected"},
        "approved": {"superseded"},
        "rejected": set(),
        "superseded": set(),
    }
    if payload.status not in allowed.get(analysis.status, set()):
        raise HTTPException(status_code=409, detail="IMPACT_ANALYSIS_INVALID_TRANSITION")
    if payload.status == "approved" and analysis.degraded:
        raise HTTPException(status_code=409, detail="DEGRADED_IMPACT_ANALYSIS_NOT_APPROVABLE")
    if payload.status == "approved":
        versions = repository.list_impact_analyses_for_event(analysis.event_id)
        approved_versions = [
            item for item in versions if item.status == "approved" and item.id != analysis.id
        ]
        latest = max(approved_versions, key=lambda item: item.version, default=None)
        if latest is not None:
            repository.update_impact_analysis(replace(latest, status="superseded"))
        supersedes_id = latest.id if latest is not None else analysis.supersedes_id
        analysis = replace(analysis, supersedes_id=supersedes_id)
    analysis = replace(analysis, status=payload.status)
    repository.update_impact_analysis(analysis)
    if payload.status == "approved":
        repository.add_outbox(
            "target_impact.recompute.requested.v1",
            analysis.id,
            {"event_id": analysis.event_id, "analysis_id": analysis.id},
        )
    saver = getattr(repository, "save_audit_log", None)
    if callable(saver):
        saver(
            AuditLog(
                id=new_id("aud"),
                actor_id=user.id,
                action="impact_analysis.transition",
                object_type="impact_analysis",
                object_id=analysis.id,
                request_id=request.state.request_id,
                details={"status": payload.status, "comment": payload.comment},
                created_at=datetime.now(timezone.utc),
            )
        )
    return DataEnvelope(
        data=ImpactAnalysisResponse.model_validate(analysis, from_attributes=True),
        meta={"request_id": request.state.request_id, "schema_version": "1.0"},
    )


@router.get("/api/v1/impact-analyses/{impact_analysis_id}/graph", response_model=DataEnvelope)
def get_impact_analysis_graph(
    impact_analysis_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    try:
        graph = ImpactAnalysisService(request.app.state.repository).graph(impact_analysis_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="IMPACT_ANALYSIS_NOT_FOUND") from exc
    return DataEnvelope(
        data=graph, meta={"request_id": request.state.request_id, "schema_version": "2.1"}
    )


@router.post("/api/v1/impact-analyses/{impact_analysis_id}/drafts", response_model=DataEnvelope)
def derive_impact_analysis_draft(
    impact_analysis_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    try:
        draft = ImpactAnalysisService(request.app.state.repository).derive_draft(
            impact_analysis_id, user.id
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="IMPACT_ANALYSIS_NOT_FOUND") from exc
    return DataEnvelope(
        data=ImpactAnalysisResponse.model_validate(draft, from_attributes=True),
        meta={"request_id": request.state.request_id, "schema_version": "2.1"},
    )


@router.patch(
    "/api/v1/impact-analyses/{impact_analysis_id}/graph",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def edit_impact_analysis_graph(
    impact_analysis_id: str,
    payload: ImpactGraphEditRequest,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    try:
        analysis = ImpactAnalysisService(request.app.state.repository).edit_graph(
            impact_analysis_id,
            expected_revision=payload.expected_revision,
            graph=payload.graph,
            scenarios=payload.scenarios,
            impact_assessments=payload.impact_assessments,
            actor=user.id,
            change_reason=payload.change_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        detail = str(exc)
        raise HTTPException(
            status_code=409 if "CONFLICT" in detail or "REQUIRED" in detail else 400, detail=detail
        ) from exc
    return DataEnvelope(
        data=ImpactAnalysisResponse.model_validate(analysis, from_attributes=True),
        meta={"request_id": request.state.request_id, "schema_version": "2.1"},
    )


@router.get("/api/v1/impact-analyses/{impact_analysis_id}/layout", response_model=DataEnvelope)
def get_impact_graph_layout(
    impact_analysis_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    layout = request.app.state.repository.get_impact_graph_layout(impact_analysis_id, user.id)
    return DataEnvelope(
        data=layout
        or {
            "analysis_id": impact_analysis_id,
            "user_id": user.id,
            "node_positions": {},
            "collapsed_groups": [],
            "viewport": {},
        },
        meta={"request_id": request.state.request_id, "schema_version": "2.1"},
    )


@router.put("/api/v1/impact-analyses/{impact_analysis_id}/layout", response_model=DataEnvelope)
def put_impact_graph_layout(
    impact_analysis_id: str,
    payload: ImpactGraphLayoutRequest,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    from app.domain import ImpactGraphLayout

    layout = ImpactGraphLayout(
        analysis_id=impact_analysis_id,
        user_id=user.id,
        node_positions=payload.node_positions,
        collapsed_groups=payload.collapsed_groups,
        viewport=payload.viewport,
        updated_at=datetime.now(timezone.utc),
    )
    request.app.state.repository.save_impact_graph_layout(layout)
    return DataEnvelope(
        data=layout, meta={"request_id": request.state.request_id, "schema_version": "2.1"}
    )


@router.delete("/api/v1/impact-analyses/{impact_analysis_id}/layout", response_model=DataEnvelope)
def delete_impact_graph_layout(
    impact_analysis_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    request.app.state.repository.delete_impact_graph_layout(impact_analysis_id, user.id)
    return DataEnvelope(
        data={"deleted": True},
        meta={"request_id": request.state.request_id, "schema_version": "2.1"},
    )


@router.get("/api/v1/impact-targets", response_model=DataEnvelope)
def list_impact_targets(
    request: Request,
    target_type: Optional[str] = Query(default=None),
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    targets = request.app.state.repository.list_impact_targets(target_type)
    return DataEnvelope(
        data=[ImpactTargetResponse.model_validate(item, from_attributes=True) for item in targets],
        meta={
            "request_id": request.state.request_id,
            "schema_version": "3.0",
            "count": len(targets),
        },
    )


@router.get("/api/v1/impact-targets/{target_id}/dashboard", response_model=DataEnvelope)
def get_impact_target_dashboard(
    target_id: str,
    request: Request,
    as_of: Optional[datetime] = Query(default=None),  # noqa: B008
    horizon: Optional[str] = Query(default=None),  # noqa: B008
    scenario_set_id: str = Query(default="baseline"),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    dashboard = ImpactAggregationService(request.app.state.repository).dashboard(
        target_id,
        as_of=as_of,
        horizon=horizon,
        scenario_set_id=scenario_set_id,
    )
    if dashboard is None:
        raise HTTPException(status_code=404, detail="IMPACT_TARGET_NOT_FOUND")
    return DataEnvelope(
        data=dashboard,
        meta={"request_id": request.state.request_id, "schema_version": "3.1"},
    )


@router.get("/api/v1/impact-targets/{target_id}/timeline", response_model=DataEnvelope)
def get_impact_target_timeline(
    target_id: str,
    request: Request,
    start: Optional[datetime] = Query(default=None),  # noqa: B008
    end: Optional[datetime] = Query(default=None),  # noqa: B008
    granularity: str = Query(default="auto", pattern="^(auto|day|week|month)$"),  # noqa: B008
    horizon: Optional[str] = Query(default=None),  # noqa: B008
    scenario_set_id: str = Query(default="baseline"),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    now = datetime.now(timezone.utc)
    timeline = ImpactAggregationService(request.app.state.repository).timeline(
        target_id,
        start=start or now - timedelta(days=180),
        end=end or now,
        granularity=granularity,
        horizon=horizon,
        scenario_set_id=scenario_set_id,
    )
    if timeline is None:
        raise HTTPException(status_code=404, detail="IMPACT_TARGET_NOT_FOUND")
    return DataEnvelope(
        data=timeline,
        meta={"request_id": request.state.request_id, "schema_version": "3.1"},
    )


@router.get("/api/v1/impact-targets/{target_id}/snapshot", response_model=DataEnvelope)
def get_impact_target_snapshot(
    target_id: str,
    request: Request,
    horizon: Optional[str] = Query(default=None),
    scenario_set_id: str = Query(default="baseline"),
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    repository = request.app.state.repository
    if repository.get_impact_target(target_id) is None:
        raise HTTPException(status_code=404, detail="IMPACT_TARGET_NOT_FOUND")
    snapshot = repository.get_latest_target_impact_snapshot(target_id, horizon, scenario_set_id)
    if snapshot is None:
        snapshot = ImpactAggregationService(repository).recompute_target(
            target_id, horizon=horizon, scenario_set_id=scenario_set_id
        )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="IMPACT_SNAPSHOT_NOT_FOUND")
    links = repository.list_target_impact_snapshot_contributions(snapshot.id)
    data = ImpactSnapshotResponse.model_validate(
        {**snapshot.__dict__, "contributions": [item.__dict__ for item in links]}
    )
    return DataEnvelope(
        data=data, meta={"request_id": request.state.request_id, "schema_version": "3.0"}
    )


@router.get("/api/v1/impact-targets/{target_id}/graph", response_model=DataEnvelope)
def get_impact_target_graph(
    target_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    graph = ImpactAggregationService(request.app.state.repository).graph(target_id)
    if not graph["nodes"]:
        raise HTTPException(status_code=404, detail="IMPACT_TARGET_NOT_FOUND")
    return DataEnvelope(
        data={"schema_version": "3.0", "legacy": False, "causal_graph": graph},
        meta={"request_id": request.state.request_id, "schema_version": "3.0"},
    )


@router.get("/api/v1/impact-targets/{target_id}", response_model=DataEnvelope)
def get_impact_target(
    target_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    target = request.app.state.repository.get_impact_target(target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="IMPACT_TARGET_NOT_FOUND")
    return DataEnvelope(
        data=ImpactTargetResponse.model_validate(target, from_attributes=True),
        meta={"request_id": request.state.request_id, "schema_version": "3.0"},
    )


@router.post("/api/v1/impact-targets/{target_id}/recompute", response_model=DataEnvelope)
def recompute_impact_target(
    target_id: str,
    request: Request,
    horizon: Optional[str] = Query(default=None),
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    snapshot = ImpactAggregationService(request.app.state.repository).recompute_target(
        target_id, horizon=horizon
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="IMPACT_TARGET_NOT_FOUND")
    return DataEnvelope(
        data=snapshot, meta={"request_id": request.state.request_id, "schema_version": "3.0"}
    )


@router.post("/api/v1/impact-projections/backfill", response_model=DataEnvelope)
def backfill_impact_projections(
    payload: ImpactProjectionBackfillRequest,
    request: Request,
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> DataEnvelope:
    if payload.as_of is not None and payload.as_of.tzinfo is None:
        raise HTTPException(status_code=422, detail="IMPACT_BACKFILL_AS_OF_TIMEZONE_REQUIRED")
    report = ImpactProjectionBackfillService(request.app.state.repository).run(as_of=payload.as_of)
    request.app.state.repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="impact_projection.backfill",
            object_type="impact_projection",
            object_id=None,
            request_id=request.state.request_id,
            details=jsonable_encoder(report),
        )
    )
    return DataEnvelope(
        data=jsonable_encoder(report),
        meta={"request_id": request.state.request_id, "schema_version": "3.0"},
    )


@router.post("/api/v1/event-impact-relations", response_model=DataEnvelope)
def create_event_impact_relation(
    payload: EventImpactRelationRequest,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    if (
        repository.get_event(payload.source_event_id) is None
        or repository.get_event(payload.target_event_id) is None
    ):
        raise HTTPException(status_code=404, detail="EVENT_NOT_FOUND")
    relation = EventImpactRelation(
        id=new_id("eir"),
        source_event_id=payload.source_event_id,
        target_event_id=payload.target_event_id,
        relation_type=payload.relation_type,
        dependency_weight=payload.dependency_weight,
        confidence=payload.confidence,
        evidence_refs=payload.evidence_refs,
        status="approved" if user.role == "admin" else "needs_review",
        created_at=datetime.now(timezone.utc),
    )
    repository.save_event_impact_relation(relation)
    return DataEnvelope(
        data=relation, meta={"request_id": request.state.request_id, "schema_version": "3.0"}
    )


@router.get("/api/v1/future-events", response_model=DataEnvelope)
def list_future_events(
    request: Request,
    start: Optional[datetime] = Query(default=None),  # noqa: B008
    end: Optional[datetime] = Query(default=None),  # noqa: B008
    target_id: Optional[str] = Query(default=None),  # noqa: B008
    event_type: Optional[str] = Query(default=None),  # noqa: B008
    kind: Optional[str] = Query(default=None),  # noqa: B008
    include_candidates: bool = Query(default=False),  # noqa: B008
    as_of: Optional[datetime] = Query(default=None),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    now = datetime.now(timezone.utc)
    start_value = start or now
    end_value = end or now + timedelta(days=90)
    if end_value < start_value:
        raise HTTPException(status_code=422, detail="FUTURE_EVENT_RANGE_INVALID")
    service = ForwardImpactService(request.app.state.repository)
    events = service.list_calendar_events(
        start=start_value,
        end=end_value,
        target_id=target_id,
        event_type=event_type,
        kinds={kind} if kind else None,
        include_candidates=include_candidates,
        as_of=as_of,
    )
    return DataEnvelope(
        data=events,
        meta={
            "request_id": request.state.request_id,
            "schema_version": "4.1",
            "count": len(events),
        },
    )


@router.post("/api/v1/future-events", response_model=DataEnvelope)
def create_future_event(
    payload: FutureEventCreateRequest,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    now = datetime.now(timezone.utc)
    event_id = new_id("fev")
    revision_id = new_id("fer")
    event = FutureEvent(
        id=event_id,
        event_type=payload.event_type,
        kind=payload.kind,
        current_revision_id=revision_id,
        created_by=user.id,
        created_at=now,
    )
    revision = FutureEventRevision(
        id=revision_id,
        future_event_id=event_id,
        revision_no=1,
        title=payload.title,
        description=payload.description,
        scheduled_from=payload.scheduled_from,
        scheduled_to=payload.scheduled_to,
        source_timezone=payload.source_timezone,
        time_precision=payload.time_precision,
        status="approved" if user.role == "admin" else "candidate",
        importance=payload.importance,
        probability_low=payload.probability_low,
        probability_base=payload.probability_base,
        probability_high=payload.probability_high,
        probability_basis=payload.probability_basis,
        source_url=payload.source_url,
        evidence_refs=payload.evidence_refs,
        available_at=now,
        created_by=user.id,
        created_at=now,
    )
    repository = request.app.state.repository
    repository.save_future_event(event)
    repository.save_future_event_revision(revision)
    for item in payload.target_impacts:
        if repository.get_impact_target(item.target_id) is None:
            raise HTTPException(status_code=404, detail="IMPACT_TARGET_NOT_FOUND")
        expected = (
            None
            if item.occurrence_probability is None
            else round(item.conditional_strength * item.occurrence_probability, 6)
        )
        repository.save_future_event_target_impact(
            FutureEventTargetImpact(
                id=new_id("fei"),
                future_event_id=event_id,
                revision_id=revision_id,
                target_id=item.target_id,
                scenario_id=item.scenario_id,
                direction=item.direction,
                magnitude=item.magnitude,
                conditional_strength=item.conditional_strength,
                occurrence_probability=item.occurrence_probability,
                expected_strength=expected,
                confidence=item.confidence,
                rationale=item.rationale,
                onset_at=item.onset_at or payload.scheduled_from,
                expected_peak_at=item.expected_peak_at,
                valid_to=item.valid_to or payload.scheduled_to,
                evidence_refs=item.evidence_refs,
                status=revision.status,
                created_at=now,
            )
        )
    return DataEnvelope(
        data={"event": event, "revision": revision},
        meta={"request_id": request.state.request_id, "schema_version": "5.0"},
    )


@router.get("/api/v1/future-events/{future_event_id}", response_model=DataEnvelope)
def get_future_event(
    future_event_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    repository = request.app.state.repository
    event = repository.get_future_event(future_event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="FUTURE_EVENT_NOT_FOUND")
    revision = (
        repository.get_future_event_revision(event.current_revision_id)
        if event.current_revision_id
        else None
    )
    return DataEnvelope(
        data={
            "event": event,
            "current_revision": revision,
            "revisions": repository.list_future_event_revisions(event.id),
            "target_impacts": repository.list_future_event_target_impacts(event_id=event.id),
        },
        meta={"request_id": request.state.request_id, "schema_version": "5.0"},
    )


@router.post("/api/v1/future-events/{future_event_id}/transition", response_model=DataEnvelope)
def transition_future_event(
    future_event_id: str,
    payload: FutureEventTransitionRequest,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    event = repository.get_future_event(future_event_id)
    if event is None or event.current_revision_id is None:
        raise HTTPException(status_code=404, detail="FUTURE_EVENT_NOT_FOUND")
    revision = repository.get_future_event_revision(event.current_revision_id)
    if revision is None or revision.revision_no != payload.expected_revision:
        raise HTTPException(status_code=409, detail="FUTURE_EVENT_REVISION_CONFLICT")
    if payload.status == "realized" and not payload.realized_event_id:
        raise HTTPException(status_code=422, detail="REALIZED_EVENT_REQUIRED")
    now = datetime.now(timezone.utc)
    updated = FutureEventRevision(
        **{
            **revision.__dict__,
            "id": new_id("fer"),
            "revision_no": revision.revision_no + 1,
            "status": payload.status,
            "change_reason": payload.change_reason,
            "supersedes_revision_id": revision.id,
            "created_by": user.id,
            "created_at": now,
            "available_at": now,
        }
    )
    repository.save_future_event_revision(updated)
    repository.save_future_event(
        FutureEvent(
            **{
                **event.__dict__,
                "current_revision_id": updated.id,
                "realized_event_id": payload.realized_event_id or event.realized_event_id,
            }
        )
    )
    return DataEnvelope(
        data={"event": repository.get_future_event(event.id), "revision": updated},
        meta={"request_id": request.state.request_id, "schema_version": "5.0"},
    )


@router.get("/api/v1/future-calendar/summary", response_model=DataEnvelope)
def future_calendar_summary(
    request: Request,
    start: datetime,
    end: datetime,
    timezone_name: str = Query(default="Asia/Shanghai", alias="timezone"),  # noqa: B008
    target_id: Optional[str] = Query(default=None),  # noqa: B008
    as_of: Optional[datetime] = Query(default=None),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    if end < start:
        raise HTTPException(status_code=422, detail="FUTURE_EVENT_RANGE_INVALID")
    try:
        data = ForwardImpactService(request.app.state.repository).calendar_summary(
            start=start, end=end, timezone_name=timezone_name, target_id=target_id, as_of=as_of
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail="FUTURE_CALENDAR_TIMEZONE_INVALID") from exc
    return DataEnvelope(
        data=data, meta={"request_id": request.state.request_id, "schema_version": "4.1"}
    )


@router.get("/api/v1/future-calendar/day", response_model=DataEnvelope)
def future_calendar_day(
    request: Request,
    selected_date: date = Query(alias="date"),  # noqa: B008
    timezone_name: str = Query(default="Asia/Shanghai", alias="timezone"),  # noqa: B008
    target_id: Optional[str] = Query(default=None),  # noqa: B008
    as_of: Optional[datetime] = Query(default=None),  # noqa: B008
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    try:
        data = ForwardImpactService(request.app.state.repository).day_view(
            selected_date=selected_date,
            timezone_name=timezone_name,
            target_id=target_id,
            as_of=as_of,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail="FUTURE_CALENDAR_TIMEZONE_INVALID") from exc
    return DataEnvelope(
        data=data, meta={"request_id": request.state.request_id, "schema_version": "4.1"}
    )


@router.post("/api/v1/forward-impact-windows", response_model=DataEnvelope)
def create_forward_impact_window(
    payload: ForwardImpactWindowCreateRequest,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    if repository.get_impact_target(payload.target_id) is None:
        raise HTTPException(status_code=404, detail="IMPACT_TARGET_NOT_FOUND")
    window = ForwardImpactWindow(
        id=new_id("fiw"),
        target_id=payload.target_id,
        as_of=payload.as_of,
        window_start=payload.window_start,
        window_end=payload.window_end,
        event_types=payload.event_types,
        catalyst_ids=payload.catalyst_ids,
        included_kinds=payload.included_kinds,
        granularity=payload.granularity,
        scenario_set_id=payload.scenario_set_id,
        created_by=user.id,
        created_at=datetime.now(timezone.utc),
    )
    try:
        ForwardImpactService(repository).create_window(window)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repository.add_outbox(
        "forward_impact.compute.requested.v1", window.id, {"window_id": window.id}
    )
    return DataEnvelope(
        data=window, meta={"request_id": request.state.request_id, "schema_version": "4.0"}
    )


@router.post("/api/v1/forward-catalysts", response_model=DataEnvelope)
def create_forward_catalyst(
    payload: ForwardCatalystCreateRequest,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    if request.app.state.repository.get_impact_target(payload.target_id) is None:
        raise HTTPException(status_code=404, detail="IMPACT_TARGET_NOT_FOUND")
    catalyst = ForwardCatalyst(
        id=new_id("fct"),
        target_id=payload.target_id,
        kind=payload.kind,
        title=payload.title,
        event_type=payload.event_type,
        scheduled_from=payload.scheduled_from,
        scheduled_to=payload.scheduled_to,
        trigger_definition=payload.trigger_definition,
        probability_low=payload.probability_low,
        probability_base=payload.probability_base,
        probability_high=payload.probability_high,
        probability_basis=payload.probability_basis,
        evidence_refs=payload.evidence_refs,
        status="approved" if user.role == "admin" else "candidate",
        created_by=user.id,
        created_at=datetime.now(timezone.utc),
    )
    request.app.state.repository.save_forward_catalyst(catalyst)
    return DataEnvelope(
        data=catalyst, meta={"request_id": request.state.request_id, "schema_version": "4.0"}
    )


@router.get("/api/v1/forward-impact-windows/{window_id}/catalysts", response_model=DataEnvelope)
def list_forward_window_catalysts(
    window_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    repository = request.app.state.repository
    window = repository.get_forward_impact_window(window_id)
    if window is None:
        raise HTTPException(status_code=404, detail="FORWARD_IMPACT_WINDOW_NOT_FOUND")
    catalysts = repository.list_forward_catalysts(window.target_id)
    if window.catalyst_ids:
        catalysts = [item for item in catalysts if item.id in window.catalyst_ids]
    return DataEnvelope(
        data=catalysts, meta={"request_id": request.state.request_id, "schema_version": "4.0"}
    )


@router.post("/api/v1/forward-catalysts/{catalyst_id}/approve", response_model=DataEnvelope)
def approve_forward_catalyst(
    catalyst_id: str,
    request: Request,
    user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    repository = request.app.state.repository
    catalyst = repository.get_forward_catalyst(catalyst_id)
    if catalyst is None:
        raise HTTPException(status_code=404, detail="FORWARD_CATALYST_NOT_FOUND")
    approved = ForwardCatalyst(**{**catalyst.__dict__, "status": "approved"})
    repository.save_forward_catalyst(approved)
    return DataEnvelope(
        data=approved, meta={"request_id": request.state.request_id, "schema_version": "4.0"}
    )


@router.get("/api/v1/forward-impact-windows/{window_id}/timeline", response_model=DataEnvelope)
def get_forward_impact_timeline(
    window_id: str,
    request: Request,
    scenario_id: str = Query(default="baseline"),
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    repository = request.app.state.repository
    if repository.get_forward_impact_window(window_id) is None:
        raise HTTPException(status_code=404, detail="FORWARD_IMPACT_WINDOW_NOT_FOUND")
    points = repository.list_forward_points(window_id, scenario_id)
    if not points:
        points = ForwardImpactService(repository).recompute(window_id)
        points = [item for item in points if item.scenario_id == scenario_id]
    return DataEnvelope(
        data=points, meta={"request_id": request.state.request_id, "schema_version": "4.0"}
    )


@router.get("/api/v1/forward-impact-windows/{window_id}/graph", response_model=DataEnvelope)
def get_forward_impact_graph(
    window_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    try:
        graph = ForwardImpactService(request.app.state.repository).graph(window_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DataEnvelope(
        data={"schema_version": "4.0", "causal_graph": graph},
        meta={"request_id": request.state.request_id, "schema_version": "4.0"},
    )


@router.post("/api/v1/forward-impact-windows/{window_id}/recompute", response_model=DataEnvelope)
def recompute_forward_impact(
    window_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    try:
        points = ForwardImpactService(request.app.state.repository).recompute(window_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DataEnvelope(
        data=points, meta={"request_id": request.state.request_id, "schema_version": "4.0"}
    )


@router.get("/api/v1/forward-impact-windows/{window_id}", response_model=DataEnvelope)
def get_forward_impact_window(
    window_id: str,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    window = request.app.state.repository.get_forward_impact_window(window_id)
    if window is None:
        raise HTTPException(status_code=404, detail="FORWARD_IMPACT_WINDOW_NOT_FOUND")
    return DataEnvelope(
        data=window, meta={"request_id": request.state.request_id, "schema_version": "4.0"}
    )


@router.post(
    "/api/v1/retrieval/retrieve",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 422),
)
def retrieve_documents(
    payload: RetrievalRetrieveRequest,
    request: Request,
    user: User = Depends(require_roles("researcher", "reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    _ = user
    repository = request.app.state.repository
    trace = RetrievalService(repository).retrieve(
        RetrievalRequest(
            query=payload.query,
            embedding_model_version=payload.embedding_model_version,
            top_k=payload.top_k,
            as_of=payload.as_of,
            chunk_types=payload.chunk_types,
            source_tiers=payload.source_tiers,
            min_score=payload.min_score,
            retrieval_mode=payload.retrieval_mode,
        )
    )
    return envelope(
        RetrievalTraceResponse.model_validate(trace, from_attributes=True),
        request.state.request_id,
    )


@router.post(
    "/api/v1/reports/{report_id}/transition",
    response_model=DataEnvelope,
    responses=openapi_error_responses(400, 401, 404, 409, 422),
)
def transition_report(
    report_id: str,
    payload: ReportTransitionRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_roles("reviewer", "publisher", "admin")),  # noqa: B008
) -> DataEnvelope:
    operation = f"report.transition:{report_id}"
    storage_key, request_hash, replay = _idempotency_begin(
        request, user, idempotency_key, operation, payload
    )
    if replay:
        return replay
    repository = request.app.state.repository
    card = repository.get_fact_card(report_id)
    if not card:
        raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND")
    # Keep transition clients compatible when they still hold the previous
    # version ID, while always deriving the new version from the latest one.
    latest = repository.get_fact_card_for_event(card.event_id)
    if latest and latest.version > card.version:
        card = latest
    allowed = {
        "needs_review": {"approved"},
        "review_required": {"approved"},
        "approved": {"published", "needs_revision"},
        "published": {"withdrawn"},
        "needs_revision": {"approved"},
        "withdrawn": set(),
    }
    if payload.status == "needs_revision" and user.role not in {"reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="REPORT_REVIEWER_REQUIRED")
    if payload.status not in allowed.get(card.status, set()):
        raise HTTPException(status_code=409, detail="REPORT_INVALID_TRANSITION")
    if payload.status == "approved" and user.role not in {"reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="REPORT_REVIEWER_REQUIRED")
    if payload.status in {"published", "withdrawn"} and user.role not in {"publisher", "admin"}:
        raise HTTPException(status_code=403, detail="REPORT_PUBLISHER_REQUIRED")
    try:
        updated = FactCardService(repository).transition(
            card, payload.status, f"state transition: {card.status} -> {payload.status}"
        )
    except ReportVersionConflict as exc:
        raise HTTPException(status_code=409, detail="REPORT_VERSION_CONFLICT") from exc
    repository.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action=f"report.{payload.status}",
            object_type="report",
            object_id=updated.id,
            request_id=request.state.request_id,
            details={"from": card.status, "to": payload.status, "supersedes": card.id},
            created_at=datetime.now(timezone.utc),
        )
    )
    response = envelope(
        FactCardResponse.model_validate(updated, from_attributes=True), request.state.request_id
    )
    return _idempotency_finish(request, storage_key, request_hash, operation, updated.id, response)


@router.get("/api/v1/briefs/daily", response_model=DataEnvelope)
def get_daily_brief(
    request: Request,
    date: Optional[str] = None,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"date"})
    from datetime import date as date_cls

    from app.publishing.briefs import BriefService

    brief_date = date_cls.today() if date is None else date_cls.fromisoformat(date)
    brief = BriefService(request.app.state.repository).generate(brief_date)
    return envelope(
        BriefResponse(
            id=brief.id,
            brief_date=brief.brief_date,
            entries=[BriefEntryResponse(**entry.__dict__) for entry in brief.entries],
            candidate_count=brief.candidate_count,
            rule_version=brief.rule_version,
        ),
        request.state.request_id,
    )
