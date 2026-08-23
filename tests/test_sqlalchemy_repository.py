from datetime import datetime, timezone

from sqlalchemy import func, select

from app.application.pipeline import EventResearchPipeline
from app.platform.db_models import OutboxModel
from app.platform.repository import SqlAlchemyRepository

SCHEMA_MAP = {
    "ingestion": None,
    "events": None,
    "evidence": None,
    "publishing": None,
    "platform": None,
    "analysis": None,
}


def payload() -> dict:
    return {
        "source_id": "szse",
        "source_tier": "S",
        "external_id": "persistent-001",
        "url": "https://example.test/persistent-001",
        "title": "示例公司（000001.SZ）2026年半年度业绩预告",
        "content": "公司预计2026年半年度净利润同比增长20%至30%。",
        "published_at": datetime(2026, 7, 12, 1, 30, tzinfo=timezone.utc),
    }


def repository(database_url: str) -> SqlAlchemyRepository:
    value = SqlAlchemyRepository(database_url, schema_translate_map=SCHEMA_MAP)
    value.create_schema_for_tests()
    return value


def test_pipeline_persists_and_reloads_after_repository_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'finsight.db'}"
    first_repository = repository(database_url)
    created = EventResearchPipeline(first_repository).process(
        idempotency_key="persist-1", **payload()
    )

    restarted_repository = repository(database_url)
    event = restarted_repository.get_event(created.event.id)
    card = restarted_repository.get_fact_card(created.fact_card.id)

    assert event is not None
    assert event.event_type == "earnings_guidance"
    assert card is not None
    assert card.claim_ids == [created.claim.id]


def test_pipeline_writes_outbox_in_same_transaction(tmp_path) -> None:
    value = repository(f"sqlite:///{tmp_path / 'outbox.db'}")
    created = EventResearchPipeline(value).process(idempotency_key="outbox-1", **payload())

    with value.session_factory() as session:
        count = session.scalar(select(func.count()).select_from(OutboxModel))
        message = session.scalar(select(OutboxModel))

    assert count == 1
    assert message is not None
    assert message.aggregate_id == created.fact_card.id
    assert message.event_type == "fact_card.created.v1"


def test_persistent_idempotency_reuses_result(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'idempotency.db'}"
    first = repository(database_url)
    created = EventResearchPipeline(first).process(idempotency_key="stable-key", **payload())

    restarted = repository(database_url)
    duplicate = EventResearchPipeline(restarted).process(idempotency_key="stable-key", **payload())

    assert duplicate.status == "duplicate"
    assert duplicate.event.id == created.event.id
    assert duplicate.fact_card.id == created.fact_card.id


def test_document_revision_persists_historical_evidence(tmp_path) -> None:
    value = repository(f"sqlite:///{tmp_path / 'revision.db'}")
    pipeline = EventResearchPipeline(value)
    first = pipeline.process(idempotency_key=None, **payload())
    changed = payload()
    changed["content"] = "公司修订预计净利润同比增长35%至45%。"
    second = pipeline.process(idempotency_key=None, **changed)

    assert second.document.id == first.document.id
    assert second.fact_card.version == 2
    assert second.evidence.revision_id != first.evidence.revision_id

    with value.transaction() as transaction:
        latest = transaction.get_latest_revision(first.document.id)
        old_evidence = transaction.get_evidence(first.evidence.id)

    assert latest is not None
    assert latest.revision_no == 2
    assert old_evidence is not None
    assert old_evidence.revision_id == first.evidence.revision_id


def test_persistent_repository_supports_evidence_document_proxies(tmp_path) -> None:
    """Provider 层必须代理 get_evidence/get_document，否则 GET /api/v1/evidence 500。"""
    value = repository(f"sqlite:///{tmp_path / 'evidence-proxy.db'}")
    created = EventResearchPipeline(value).process(idempotency_key="evd-proxy", **payload())

    evidence = value.get_evidence(created.evidence.id)
    document = value.get_document(created.document.id)

    assert evidence is not None
    assert evidence.id == created.evidence.id
    assert document is not None
    assert document.id == created.document.id


def test_persistent_repository_supports_user_and_audit_proxies(tmp_path) -> None:
    """SqlAlchemyRepository（Provider 层）必须代理用户与审计方法，否则登录链路 500。"""
    from app.domain import AuditLog, User
    from app.platform.ids import new_id

    value = repository(f"sqlite:///{tmp_path / 'auth.db'}")
    user = User(
        id=new_id("usr"),
        username="admin",
        password_hash="$argon2id$fake",
        role="admin",
        status="active",
    )
    value.save_user(user)

    found = value.get_user_by_username("admin")
    assert found is not None
    assert found.id == user.id
    assert value.get_user(user.id).username == "admin"

    value.save_audit_log(
        AuditLog(
            id=new_id("aud"),
            actor_id=user.id,
            action="auth.login",
            object_type="user",
            object_id=user.id,
            request_id="req_1",
            details={},
            created_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        )
    )
    logs = value.list_audit_logs()
    assert len(logs) == 1
    assert logs[0].action == "auth.login"


def test_persistent_repository_creates_and_updates_impact_graph_layout(tmp_path) -> None:
    from app.domain import ImpactGraphLayout

    value = repository(f"sqlite:///{tmp_path / 'impact-layout.db'}")
    value.save_impact_graph_layout(
        ImpactGraphLayout(
            analysis_id="ian_1", user_id="usr_1",
            node_positions={"node-1": {"x": 10.0, "y": 20.0}},
        )
    )
    value.save_impact_graph_layout(
        ImpactGraphLayout(
            analysis_id="ian_1", user_id="usr_1",
            node_positions={"node-1": {"x": 30.0, "y": 40.0}},
        )
    )

    layout = value.get_impact_graph_layout("ian_1", "usr_1")
    assert layout is not None
    assert layout.node_positions["node-1"] == {"x": 30.0, "y": 40.0}


def test_persistent_repository_supports_brief_and_tool_call_proxies(tmp_path) -> None:
    from app.domain import Brief, ToolCall
    from app.platform.ids import new_id

    value = repository(f"sqlite:///{tmp_path / 'brief.db'}")
    value.save_brief(
        Brief(
            id=new_id("brf"),
            brief_date="2026-07-12",
            entries=[],
            candidate_count=0,
            rule_version="brief-v1",
        )
    )
    assert value.get_brief_by_date("2026-07-12") is not None
    assert value.list_published_reports(
        datetime(2026, 7, 12, tzinfo=timezone.utc),
        datetime(2026, 7, 13, tzinfo=timezone.utc),
    ) == []

    value.save_tool_call(
        ToolCall(
            id=new_id("tlc"),
            workflow_id="wfr_1",
            agent_type="company_analyst",
            tool_name="calculate_financial_metrics",
            arguments={},
            result={"items": []},
            as_of=datetime(2026, 7, 12, tzinfo=timezone.utc),
            status="succeeded",
        )
    )
    calls = value.list_tool_calls("wfr_1")
    assert len(calls) == 1
    assert calls[0].tool_name == "calculate_financial_metrics"


def test_event_type_registry_increment_and_reload(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'registry.db'}"
    first = repository(database_url)
    first.increment_event_type_registry_count("weather_event")
    first.increment_event_type_registry_count("weather_event")

    restarted = repository(database_url)
    entry = restarted.get_event_type_registry("weather_event")
    assert entry is not None
    assert entry.status == "candidate"
    assert entry.event_count == 2
    listed = restarted.list_event_type_registry(status="candidate")
    assert [item.type_label for item in listed] == ["weather_event"]
