import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.agents.registry import AgentRegistry
from app.analysis.service import ImpactAnalysisService
from app.api.auth import PASSWORD_HASH, require_roles
from app.api.errors import openapi_error_responses
from app.api.schemas import (
    AdminMetricsResponse,
    AuditLogResponse,
    BriefEntryResponse,
    BriefResponse,
    BudgetLedgerEntryResponse,
    ClaimResponse,
    ConflictResponse,
    DataEnvelope,
    DocumentUpdateRequest,
    EventDetailResponse,
    EventResponse,
    EvidenceResponse,
    FactCardResponse,
    ImpactAnalysisResponse,
    IngestDocumentRequest,
    IngestRunResponse,
    LlmAgentBindingBulkRequest,
    LlmAgentBindingRequest,
    LlmProviderCreateRequest,
    LlmProviderRotateKeyRequest,
    LlmProviderUpdateRequest,
    LoginRequest,
    LoginResponse,
    MergeReviewDecisionRequest,
    MergeReviewTaskResponse,
    NodeAttemptResponse,
    PipelineResponse,
    ReportTransitionRequest,
    ResearchCreateRequest,
    ResearchPlanResponse,
    ResearchTaskResponse,
    RetrievalRetrieveRequest,
    RetrievalTraceResponse,
    ReviewDecisionRequest,
    ReviewTaskResponse,
    SourceCreateRequest,
    SourceHealthResponse,
    SourceResponse,
    SourceUpdateRequest,
    WorkflowCreateRequest,
    WorkflowResponse,
    WorkflowResumeRequest,
)
from app.domain import AuditLog, RetrievalRequest, Source, User
from app.ingestion.html_text import scrub_extracted_text
from app.ingestion.seed_sources import seed_sources
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
    responses=openapi_error_responses(401, 422),
)
def login(payload: LoginRequest, request: Request) -> DataEnvelope:
    user = request.app.state.repository.get_user_by_username(payload.username)
    if (
        not user
        or user.status != "active"
        or not PASSWORD_HASH.verify(payload.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="AUTH_INVALID_CREDENTIALS")
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
    status_filter: Annotated[
        Optional[str], Query(pattern="^(pending|decided)$")
    ] = None,
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
    return _idempotency_finish(
        request, storage_key, request_hash, operation, updated.id, response
    )


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


@router.get("/api/v1/research/{plan_id}/tasks", response_model=DataEnvelope)
def list_research_tasks(
    plan_id: str,
    request: Request,
    _user: User = Depends(require_roles(*BUSINESS_ROLES)),  # noqa: B008
) -> DataEnvelope:
    if not request.app.state.repository.get_research_plan(plan_id):
        raise HTTPException(status_code=404, detail="RESEARCH_PLAN_NOT_FOUND")
    tasks = request.app.state.repository.list_research_tasks(plan_id)
    return envelope(
        [ResearchTaskResponse.model_validate(t, from_attributes=True) for t in tasks],
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
    citation_completeness = (
        claims_with_evidence / total_claims if total_claims else None
    )

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
    return _idempotency_finish(
        request, storage_key, request_hash, operation, updated.id, response
    )


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


@router.get("/api/v1/merge-reviews", response_model=DataEnvelope)
def list_merge_reviews(
    request: Request,
    status_filter: Annotated[
        Optional[str], Query(pattern="^(open|decided)$")
    ] = None,
    cursor: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    _user: User = Depends(require_roles("reviewer", "admin")),  # noqa: B008
) -> DataEnvelope:
    _validate_query(request, {"status_filter", "cursor", "limit"})
    _validate_cursor(cursor)
    tasks = request.app.state.repository.list_merge_review_tasks(
        status=status_filter, limit=limit + 1, cursor=cursor
    )
    values = [
        MergeReviewTaskResponse.model_validate(task, from_attributes=True)
        for task in tasks
    ]
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
    return _idempotency_finish(
        request, storage_key, request_hash, operation, updated.id, response
    )


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
    source = (
        repository.get_source(document.source_id)
        if document is not None
        else None
    )
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
    return _idempotency_finish(
        request, storage_key, request_hash, operation, task.id, response
    )


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
    return _idempotency_finish(
        request, storage_key, request_hash, operation, source.id, response
    )


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
    return _idempotency_finish(
        request, storage_key, request_hash, operation, source.id, response
    )


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
    return _idempotency_finish(
        request, storage_key, request_hash, operation, "sources", response
    )


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
    return _idempotency_finish(
        request, storage_key, request_hash, operation, updated.id, response
    )


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
    return _idempotency_finish(
        request, storage_key, request_hash, operation, "sources", response
    )


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
    providers = request.app.state.repository.list_llm_providers(
        limit=limit + 1, cursor=cursor
    )
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


def envelope(
    data: object, request_id: str, *, next_cursor: Optional[str] = None
) -> DataEnvelope:
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
                "^(draft|needs_review|review_required|approved|published|"
                "needs_revision|withdrawn)$"
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


@router.get(
    "/api/v1/events/{event_id}/impact-analysis/versions", response_model=DataEnvelope
)
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
    return _idempotency_finish(
        request, storage_key, request_hash, operation, updated.id, response
    )


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
