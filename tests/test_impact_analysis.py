"""事件影响分析模块测试。"""

from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.analysis.agents import ImpactAnalystAgent
from app.analysis.schemas import ImpactAnalysisOutput, ImpactTarget, TransmissionChain
from app.analysis.service import ImpactAnalysisService
from app.api.auth import PASSWORD_HASH
from app.domain import Document, Event, User
from app.events.classifier import EventClassifier
from app.main import create_app
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository


def _make_repo() -> InMemoryRepository:
    return InMemoryRepository()


def _macro_document() -> Document:
    return Document(
        id=new_id("doc"),
        source_id="src_test",
        source_tier="S",
        external_id="ext-us-fed-001",
        canonical_url="https://example.test/fed",
        title="美联储宣布加息25个基点",
        content="美联储在2026年8月5日的利率决议中宣布将联邦基金利率目标区间上调25个基点至5.25%-5.50%。",
        content_hash="hash",
        published_at=datetime.now(timezone.utc),
        ingested_at=datetime.now(timezone.utc),
    )


def _save_event(repo: InMemoryRepository) -> Event:
    event = Event(
        id=new_id("evt"),
        event_type="macro_policy",
        status="triaged",
        title="美联储宣布加息25个基点",
        entity_ids=[],
        document_ids=[],
        importance=0.92,
        urgency="high",
        occurred_at=datetime.now(timezone.utc),
        key_fields={
            "policy_body": "federal_reserve",
            "rate_decision": "hike",
            "rate_change_bp": "25",
            "target_rate": "5.25%-5.50%",
            "effective_date": "2026-08-05",
        },
        missing_required=[],
    )
    repo.save_event(event)
    return event


def test_macro_policy_classification() -> None:
    document = _macro_document()
    result = EventClassifier().classify(document)
    assert result.event_type == "macro_policy"
    assert result.key_fields["policy_body"] == "federal_reserve"
    assert result.key_fields["rate_decision"] == "hike"
    assert result.key_fields["rate_change_bp"] == "25"


def test_macro_policy_hold_classification() -> None:
    document = Document(
        id=new_id("doc"),
        source_id="src_test",
        source_tier="S",
        external_id="ext-ecb-001",
        canonical_url="https://example.test/ecb",
        title="欧洲央行维持利率不变",
        content="欧洲央行在2026年8月5日的利率决议中决定维持主要再融资利率在4.25%不变。",
        content_hash="hash",
        published_at=datetime.now(timezone.utc),
        ingested_at=datetime.now(timezone.utc),
    )
    result = EventClassifier().classify(document)
    assert result.event_type == "macro_policy"
    assert result.key_fields["policy_body"] == "ecb"
    assert result.key_fields["rate_decision"] == "hold"


def test_impact_analysis_service_fallback() -> None:
    repo = _make_repo()
    event = _save_event(repo)

    analysis = ImpactAnalysisService(repo).generate(event.id)

    assert analysis.event_id == event.id
    assert analysis.version == 1
    assert analysis.degraded is True
    assert analysis.status == "draft"
    assert analysis.summary
    assert len(analysis.impacts) >= 1
    assert analysis.generated_by == "agent:impact_analyst"
    assert analysis.quality_report["blockers"] == ["model_unavailable"]
    assert analysis.quality_report["model_failure"]["code"] in {
        "schema_invalid",
        "invoke_error",
    }


def test_impact_analysis_records_timeout_root_cause(caplog) -> None:
    repo = _make_repo()
    event = _save_event(repo)

    class TimeoutProvider:
        name = "timeout"
        model = "test"
        estimated_cost_usd = 0.0

        def invoke(self, request):
            raise TimeoutError("gateway timeout")

    from app.model_gateway.service import ModelGateway

    agent = ImpactAnalystAgent(ModelGateway(repo, provider=TimeoutProvider()))
    with caplog.at_level("WARNING"):
        analysis = ImpactAnalysisService(repo, agent=agent).generate(event.id)

    assert analysis.degraded is True
    failure = analysis.quality_report["model_failure"]
    assert failure["code"] == "timeout"
    assert failure["exception_type"] == "TimeoutError"
    assert failure["stage"] == "invoke"
    assert "gateway timeout" in caplog.text
    audits = [
        item for item in repo.list_audit_logs() if item.action == "impact_analysis.generated"
    ]
    assert audits[-1].details["model_failure"]["code"] == "timeout"


def test_impact_analysis_records_schema_parse_failure(caplog) -> None:
    repo = _make_repo()
    event = _save_event(repo)

    class InvalidPayloadProvider:
        name = "invalid"
        model = "test"
        estimated_cost_usd = 0.0

        def invoke(self, request):
            return {"not": "an impact analysis"}

    from app.model_gateway.service import ModelGateway

    agent = ImpactAnalystAgent(ModelGateway(repo, provider=InvalidPayloadProvider()))
    with caplog.at_level("WARNING"):
        analysis = ImpactAnalysisService(repo, agent=agent).generate(event.id)

    assert analysis.degraded is True
    failure = analysis.quality_report["model_failure"]
    assert failure["code"] == "schema_invalid"
    assert failure["stage"] == "schema"
    assert "schema_invalid" in caplog.text


def test_impact_analysis_service_with_mock_agent() -> None:
    repo = _make_repo()
    event = _save_event(repo)

    output = ImpactAnalysisOutput(
        summary="加息压制成长股估值，利好银行息差。",
        transmission_chains=[
            TransmissionChain(
                chain_id="chn_test",
                mechanism="利率-估值传导",
                steps=[{"step": 0, "description": "美联储加息"}],
                confidence=0.7,
            )
        ],
        impacts=[
            ImpactTarget(
                target_type="sector",
                target_name="银行",
                direction="positive",
                magnitude="moderate",
                horizon="medium",
                confidence=0.7,
                rationale="息差扩大",
            )
        ],
        macro_assumptions=["通胀可控"],
        watch_items=["就业数据"],
        confidence=0.7,
    )

    agent = ImpactAnalystAgent.__new__(ImpactAnalystAgent)
    agent.analyze = lambda *args, **kwargs: output

    analysis = ImpactAnalysisService(repo, agent=agent).generate(event.id)

    assert analysis.degraded is False
    assert analysis.summary == output.summary
    assert analysis.impacts[0]["target_name"] == "银行"


def test_impact_analysis_version_chain() -> None:
    repo = _make_repo()
    event = _save_event(repo)
    service = ImpactAnalysisService(repo)

    first = service.generate(event.id)
    second = service.generate(event.id)

    assert first.version == 1
    assert second.version == 2
    assert second.supersedes_id is None
    updated_first = repo.get_impact_analysis(first.id)
    assert updated_first is not None
    assert updated_first.status == "draft"
    latest = repo.get_latest_impact_analysis_for_event(event.id)
    assert latest is not None
    assert latest.id == second.id


def test_impact_analysis_list_versions() -> None:
    repo = _make_repo()
    event = _save_event(repo)
    service = ImpactAnalysisService(repo)

    service.generate(event.id)
    service.generate(event.id)

    versions = service.list_versions(event.id)
    assert len(versions) == 2
    assert versions[0].version > versions[1].version

@contextmanager
def _admin_client():
    with TestClient(create_app()) as client:
        repository = client.app.state.repository
        admin = User(
            id=new_id("usr"),
            username="impact-admin",
            password_hash=PASSWORD_HASH.hash("secret"),
            role="admin",
            status="active",
        )
        repository.save_user(admin)
        login = client.post(
            "/api/v1/auth/login", json={"username": "impact-admin", "password": "secret"}
        )
        token = login.json()["data"]["access_token"]
        yield client, repository, token


def test_api_generate_impact_analysis() -> None:
    with _admin_client() as (client, repository, token):
        event = _save_event(repository)
        response = client.post(
            f"/api/v1/events/{event.id}/impact-analysis",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["event_id"] == event.id
        assert data["version"] == 1
        assert data["degraded"] is True
        assert data["status"] == "draft"
        assert len(data["impacts"]) >= 1


def test_api_get_impact_analysis() -> None:
    with _admin_client() as (client, repository, token):
        event = _save_event(repository)
        service = ImpactAnalysisService(repository)
        analysis = service.generate(event.id)

        response = client.get(
            f"/api/v1/events/{event.id}/impact-analysis",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["id"] == analysis.id
        assert data["summary"]


def test_api_impact_analysis_transition_blocks_degraded_approval() -> None:
    with _admin_client() as (client, repository, token):
        event = _save_event(repository)
        analysis = ImpactAnalysisService(repository).generate(event.id)
        response = client.post(
            f"/api/v1/impact-analyses/{analysis.id}/transition",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "needs_review", "comment": "补充证据后复核"},
        )
        assert response.status_code == 200

        response = client.post(
            f"/api/v1/impact-analyses/{analysis.id}/transition",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "approved", "comment": "reviewed"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "DEGRADED_IMPACT_ANALYSIS_NOT_APPROVABLE"


def test_api_list_impact_analysis_versions() -> None:
    with _admin_client() as (client, repository, token):
        event = _save_event(repository)
        service = ImpactAnalysisService(repository)
        service.generate(event.id)
        service.generate(event.id)

        response = client.get(
            f"/api/v1/events/{event.id}/impact-analysis/versions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 2


def test_api_impact_analysis_not_found() -> None:
    with _admin_client() as (client, repository, token):
        event = _save_event(repository)
        response = client.get(
            f"/api/v1/events/{event.id}/impact-analysis",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "IMPACT_ANALYSIS_NOT_FOUND"


def test_api_impact_analysis_pending() -> None:
    with _admin_client() as (client, repository, token):
        event = _save_event(repository)
        repository.add_outbox(
            "impact_analysis.requested.v1",
            event.id,
            {"event_id": event.id, "trigger": "test"},
        )
        response = client.get(
            f"/api/v1/events/{event.id}/impact-analysis",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 202
        data = response.json()["data"]
        assert data["status"] == "pending"
