from datetime import datetime, timezone

from app.domain import Document, Event
from app.events.router import RouterDecision
from app.ood import OODDetectionService
from app.ood_learning import OODLearningService
from app.platform.repository import InMemoryRepository


def _document() -> Document:
    now = datetime.now(timezone.utc)
    return Document(
        id="doc-ood",
        source_id="wire",
        source_tier="B",
        external_id="ood-1",
        canonical_url="https://example.test/ood-1",
        title="云服务故障导致券商交易终端和支付系统中断",
        content="交易所和券商表示，部分金融市场服务受到影响，流动性可能下降。",
        content_hash="ood-hash",
        published_at=now,
        ingested_at=now,
    )


def test_relevant_unknown_event_creates_ood_observation() -> None:
    repository = InMemoryRepository()
    document = _document()
    event = Event(
        id="evt-ood",
        event_type="financial_infrastructure_disruption",
        status="needs_review",
        title=document.title,
        entity_ids=[],
        document_ids=[document.id],
        importance=0.8,
        urgency="high",
        occurred_at=document.published_at,
        classifier_version="candidate",
    )
    decision = RouterDecision(
        relevance="relevant",
        event_type=event.event_type,
        confidence=0.55,
        required_agents=("fact_checker",),
        reason="unknown financial infrastructure event",
        is_candidate_type=True,
    )
    service = OODDetectionService(repository)
    detection = service.detect(document, decision)
    observation = service.observe(document, event, decision, detection)

    assert detection.is_ood is True
    assert observation is not None
    assert observation.status == "ready_for_clustering"
    assert repository.get_ood_observation(observation.id) == observation


def test_unknown_non_financial_event_does_not_enter_ood_queue() -> None:
    repository = InMemoryRepository()
    document = _document()
    decision = RouterDecision(
        relevance="irrelevant",
        event_type="unknown_event",
        confidence=0.3,
        is_candidate_type=True,
    )
    detection = OODDetectionService(repository).detect(document, decision)
    assert detection.is_ood is False


def test_ood_learning_clusters_proposes_and_evaluates_pack() -> None:
    repository = InMemoryRepository()
    document = _document()
    event = Event(
        id="evt-ood-2",
        event_type="financial_infrastructure_disruption",
        status="needs_review",
        title=document.title,
        entity_ids=[],
        document_ids=[document.id],
        importance=0.8,
        urgency="high",
        occurred_at=document.published_at,
        classifier_version="candidate",
    )
    decision = RouterDecision(
        relevance="relevant",
        event_type=event.event_type,
        confidence=0.55,
        is_candidate_type=True,
    )
    detection_service = OODDetectionService(repository)
    observation = detection_service.observe(
        document,
        event,
        decision,
        detection_service.detect(document, decision),
    )
    assert observation is not None
    learning = OODLearningService(repository)
    clusters = learning.cluster_ready_observations()
    proposal = learning.propose_type(clusters[0])
    pack = learning.build_candidate_pack(proposal)
    evaluation = learning.evaluate_pack(pack)

    assert proposal.status == "draft"
    assert pack.manifest.status == "candidate"
    assert evaluation.recommendation == "promote_to_shadow"
