"""Impact Context Builder 时间截面与证据过滤测试。"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.analysis.context import ImpactContextBuilder
from app.domain import Claim, Event, FactCard
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository


class _NoopRetrieval:
    def retrieve(self, request):
        return SimpleNamespace(items=[])


def test_context_builder_excludes_unverified_and_future_claims() -> None:
    repo = InMemoryRepository()
    as_of = datetime(2026, 8, 1, tzinfo=timezone.utc)
    event = Event(
        id=new_id("evt"), event_type="macro_policy", status="triaged",
        title="政策事件", entity_ids=[], document_ids=[], importance=0.8,
        urgency="high", occurred_at=as_of,
    )
    repo.save_event(event)
    repo.save_claim(Claim(
        id=new_id("clm"), event_id=event.id, subject_text="政策",
        predicate="announced", object_value={"value": "hike"}, status="verified",
        confidence=0.9, evidence_ids=["ev_1"], as_of=as_of,
    ))
    repo.save_claim(Claim(
        id=new_id("clm"), event_id=event.id, subject_text="未来",
        predicate="announced", object_value={"value": "unknown"}, status="verified",
        confidence=0.9, evidence_ids=[],
        as_of=datetime(2026, 8, 2, tzinfo=timezone.utc),
    ))
    repo.save_claim(Claim(
        id=new_id("clm"), event_id=event.id, subject_text="未核验",
        predicate="announced", object_value={"value": "rumor"}, status="candidate",
        confidence=0.4, evidence_ids=[], as_of=as_of,
    ))
    repo.save_fact_card(FactCard(
        id=new_id("rpt"), event_id=event.id, version=1, status="published",
        title=event.title, summary="事实摘要", claim_ids=[], as_of=as_of,
    ))

    context = ImpactContextBuilder(repo, retrieval=_NoopRetrieval()).build(event)
    assert len(context.claims) == 1
    assert context.claims[0].object_value["value"] == "hike"
    assert context.fact_card is not None
    assert "unverified_claims_excluded" in context.warnings
    assert context.data_availability["market_data"] == "unavailable"
    assert context.to_payload()["as_of"] == as_of.isoformat()
