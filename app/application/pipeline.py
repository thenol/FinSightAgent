import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from app.document_intelligence.service import DocumentIntelligenceService
from app.domain import AuditLog, MergeReviewTask, PipelineResult, WorkflowRun
from app.events.matching import EventMatcher
from app.events.router import EventRouter
from app.events.service import EventService
from app.evidence.service import EvidenceService
from app.ingestion.artifacts import ArtifactStore, InMemoryArtifactStore
from app.ingestion.service import IngestionService
from app.model_gateway.service import ModelGateway
from app.platform.ids import new_id
from app.platform.repository import PipelineResultReference, RepositoryProvider
from app.platform.settings import Settings
from app.publishing.service import FactCardService
from app.review.service import AutoReviewService


class EventResearchPipeline:
    def __init__(
        self,
        repository: RepositoryProvider,
        artifact_store: Optional[ArtifactStore] = None,
        settings: Optional[Settings] = None,
        *,
        model_gateway: Optional[ModelGateway] = None,
        event_router: Optional[EventRouter] = None,
    ) -> None:
        self.repository = repository
        self.artifact_store = artifact_store or InMemoryArtifactStore()
        self.settings = settings
        self._model_gateway_override = model_gateway
        self._event_router_override = event_router

    def process(
        self,
        *,
        idempotency_key: Optional[str],
        source_id: str,
        source_tier: str,
        external_id: Optional[str],
        url: Optional[str],
        title: str,
        content: str,
        published_at: datetime,
    ) -> PipelineResult:
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "source_id": source_id,
                    "source_tier": source_tier,
                    "external_id": external_id,
                    "url": url,
                    "title": title,
                    "content": content,
                    "published_at": published_at.isoformat(),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self.repository.transaction() as repository:
            ingestion = IngestionService(repository, self.artifact_store)
            events = EventService(repository)
            matcher = EventMatcher(repository)
            evidence_service = EvidenceService(repository)
            fact_cards = FactCardService(repository)

            if idempotency_key:
                previous = repository.get_idempotent(idempotency_key)
                if previous:
                    if previous.request_hash != request_hash:
                        raise ValueError("IDEMPOTENCY_CONFLICT")
                    document = repository.get_document(previous.document_id)
                    event = repository.get_event(previous.event_id)
                    card = repository.get_fact_card(previous.fact_card_id)
                    if not document or not event or not card:
                        raise RuntimeError("IDEMPOTENCY_REFERENCE_MISSING")
                    claim = repository.get_claim(card.claim_ids[0])
                    if not claim:
                        raise RuntimeError("CLAIM_REFERENCE_MISSING")
                    evidence = repository.get_evidence(claim.evidence_ids[0])
                    if not evidence:
                        raise RuntimeError("EVIDENCE_REFERENCE_MISSING")
                    return PipelineResult("duplicate", document, event, evidence, claim, card)

            document, ingestion_status = ingestion.ingest(
                source_id=source_id,
                source_tier=source_tier,
                external_id=external_id,
                url=url,
                title=title,
                content=content,
                published_at=published_at,
            )
            existing_event = None
            if ingestion_status in {"duplicate", "revised"}:
                existing_event = repository.find_event_by_document(document.id)
                if existing_event and ingestion_status == "duplicate":
                    card = repository.get_fact_card_for_event(existing_event.id)
                    if card:
                        claim = repository.get_claim(card.claim_ids[0])
                        if not claim:
                            raise RuntimeError("CLAIM_REFERENCE_MISSING")
                        evidence = repository.get_evidence(claim.evidence_ids[0])
                        if not evidence:
                            raise RuntimeError("EVIDENCE_REFERENCE_MISSING")
                        return PipelineResult(
                            "duplicate", document, existing_event, evidence, claim, card
                        )

            is_new_event = False
            # Router/ModelGateway 必须挂在当前事务 repository 上，避免 SQLite 嵌套锁
            gateway = self._model_gateway_override or ModelGateway(repository)
            router = self._event_router_override or EventRouter(gateway)
            if existing_event:
                event = events.attach_document_to_event(existing_event, document)
                disclosure_group_id = event.disclosure_group_id
            else:
                doc_intel = DocumentIntelligenceService(repository)
                _, _, _, disclosure_group = doc_intel.process(document)
                disclosure_group_id = disclosure_group.id

                # 规则提名 → Router v2 相关性裁决 → relevant 的一等/候选类型进入可研究状态
                rule_hint = router.propose(document)
                router_decision = router.route(document, rule_hint=rule_hint)
                classification = router.merge_classification(rule_hint, router_decision)
                matched_event, features, candidate_event = matcher.find_match(
                    document,
                    classification.event_type,
                    classification.key_fields,
                    disclosure_group_id=disclosure_group_id,
                )
                decision = matcher.decide(features)
                matcher.record_decision(document, candidate_event, features, decision)
                if matched_event:
                    event = events.attach_document_to_event(matched_event, document)
                else:
                    event = events.create_event(
                        document,
                        classification=classification,
                        disclosure_group_id=disclosure_group_id,
                    )
                    is_new_event = True
                    if decision == "review" and candidate_event:
                        task = MergeReviewTask(
                            id=new_id("mrt"),
                            document_id=document.id,
                            candidates=[candidate_event.id],
                            status="open",
                        )
                        repository.save_merge_review_task(task)
                        AutoReviewService(repository).attempt_merge_review(task)
                    repository.save_audit_log(
                        AuditLog(
                            id=new_id("aud"),
                            actor_id="system",
                            action="event.router_decision",
                            object_type="event",
                            object_id=event.id,
                            request_id=None,
                            details={
                                "relevance": router_decision.relevance,
                                "event_type": router_decision.event_type,
                                "importance": router_decision.importance,
                                "is_candidate_type": router_decision.is_candidate_type,
                                "rule_hint_type": router_decision.rule_hint_type,
                                "confidence": router_decision.confidence,
                                "required_agents": list(router_decision.required_agents),
                                "reason": router_decision.reason,
                                "model_run_id": router_decision.model_run_id,
                                "used_fallback": router_decision.used_fallback,
                                "router_schema_version": "v2",
                            },
                            created_at=datetime.now(timezone.utc),
                        )
                    )
            # 业务 Claim 按 Schema 批量生成；FactCard 目前仍以首个主 Claim 为入口，
            # 其余 Claim 已持久化并可供工作流/核验读取。
            evidence, business_claims = evidence_service.register_event_claims(document, event)
            if not business_claims:
                raise RuntimeError("NO_SOURCE_TEXT")
            claim = business_claims[0]
            card = fact_cards.create(event, claim)
            result = PipelineResult(ingestion_status, document, event, evidence, claim, card)
            if idempotency_key:
                repository.save_idempotent(
                    idempotency_key,
                    PipelineResultReference(document.id, event.id, card.id, request_hash),
                )
            repository.add_outbox(
                "fact_card.revised.v1" if ingestion_status == "revised" else "fact_card.created.v1",
                card.id,
                {"event_id": event.id, "fact_card_id": card.id, "status": card.status},
            )
            self._maybe_auto_trigger_workflow(repository, event, is_new_event, document.source_id)
            return result

    def _maybe_auto_trigger_workflow(
        self,
        repository: RepositoryProvider,
        event,
        is_new_event: bool,
        source_id: str,
    ) -> None:
        """高重要度新事件自动进入研究工作流（pending），由 workflow worker 或 Admin 启动运行。"""
        if not self.settings or not self.settings.workflow_auto_trigger_enabled:
            return
        if not is_new_event:
            return
        if event.status in {"dormant", "archived"}:
            return
        if event.importance < self.settings.workflow_auto_importance_threshold:
            return
        # 避免同一事件重复创建（例如重试或并发）
        if repository.list_workflow_runs(event_id=event.id, limit=1):
            return

        run = WorkflowRun(
            id=new_id("wfr"),
            event_id=event.id,
            trigger_id="auto",
            status="pending",
            as_of=event.occurred_at or datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        repository.save_workflow_run(run)
        repository.add_outbox(
            "workflow.created.v1",
            run.id,
            {"event_id": event.id, "trigger_id": "auto", "importance": event.importance},
        )
        repository.save_audit_log(
            AuditLog(
                id=new_id("aud"),
                actor_id="system",
                action="workflow.auto_create",
                object_type="workflow",
                object_id=run.id,
                request_id=None,
                details={
                    "event_id": event.id,
                    "source_id": source_id,
                    "importance": event.importance,
                    "threshold": self.settings.workflow_auto_importance_threshold,
                },
                created_at=datetime.now(timezone.utc),
            )
        )