"""审核中心处理 claim_conflict 类型审核任务测试。"""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.auth import PASSWORD_HASH
from app.domain import Claim, ConflictRecord, Event, ReviewTask, User
from app.main import create_app
from app.platform.ids import new_id


def _make_event() -> Event:
    return Event(
        id=new_id("evt"),
        event_type="earnings_guidance",
        status="researchable",
        title="Earnings",
        entity_ids=[],
        document_ids=[],
        importance=0.8,
        urgency="high",
        occurred_at=datetime.now(timezone.utc),
        version=1,
        confidence=0.9,
        key_fields={"profit_forecast": "1.0B"},
    )


def _make_claim(event_id: str, fingerprint: str, value: str) -> Claim:
    return Claim(
        id=new_id("clm"),
        event_id=event_id,
        subject_text="Company",
        predicate="profit_forecast",
        object_value={"value": value},
        fingerprint=fingerprint,
        status="conflicted",
        confidence=Decimal("0.30"),
        evidence_ids=[new_id("evd")],
        as_of=datetime.now(timezone.utc),
    )


def test_decide_claim_conflict_approve_restores_claims(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_ENV", "test")
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        repository.save_user(
            User(
                id=new_id("usr"),
                username="reviewer-admin",
                password_hash=PASSWORD_HASH.hash("secret"),
                role="admin",
            )
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "reviewer-admin", "password": "secret"},
        )
        assert login.status_code == 200
        token = login.json()["data"]["access_token"]

        event = _make_event()
        repository.save_event(event)
        left = _make_claim(event.id, "fp-left", "1.0B")
        right = _make_claim(event.id, "fp-right", "2.0B")
        repository.save_claim(left)
        repository.save_claim(right)

        conflict = ConflictRecord(
            id=new_id("cfl"),
            event_id=event.id,
            conflict_type="value",
            severity="critical",
            status="open",
            summary="Profit forecast mismatch",
            claim_ids=[left.id, right.id],
        )
        repository.save_conflict(conflict)

        task = ReviewTask(
            id=new_id("rvt"),
            object_type="claim_conflict",
            object_id=conflict.id,
            reason_code="CONFLICT_VALUE",
            allowed_decisions=["approve", "reject"],
            status="pending",
        )
        repository.save_review_task(task)

        response = client.post(
            f"/api/v1/reviews/{task.id}/decision",
            headers={"Authorization": f"Bearer {token}"},
            json={"decision": "approve", "comment": "approved after review"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["conflict_id"] == conflict.id
        assert data["resolution"] == "approve"

        updated_conflict = repository.get_conflict(conflict.id)
        assert updated_conflict is not None
        assert updated_conflict.status == "resolved"
        assert updated_conflict.resolution == "approve"

        updated_left = repository.get_claim(left.id)
        updated_right = repository.get_claim(right.id)
        assert updated_left is not None and updated_left.status == "verified"
        assert updated_right is not None and updated_right.status == "verified"


def test_decide_claim_conflict_reject_marks_claims_rejected(monkeypatch) -> None:
    monkeypatch.setenv("FINSIGHT_ENV", "test")
    monkeypatch.setenv("FINSIGHT_REPOSITORY", "memory")
    application = create_app()
    with TestClient(application) as client:
        repository = client.app.state.repository
        repository.save_user(
            User(
                id=new_id("usr"),
                username="reviewer-admin",
                password_hash=PASSWORD_HASH.hash("secret"),
                role="admin",
            )
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "reviewer-admin", "password": "secret"},
        )
        token = login.json()["data"]["access_token"]

        event = _make_event()
        repository.save_event(event)
        left = _make_claim(event.id, "fp-left-2", "1.0B")
        right = _make_claim(event.id, "fp-right-2", "2.0B")
        repository.save_claim(left)
        repository.save_claim(right)

        conflict = ConflictRecord(
            id=new_id("cfl"),
            event_id=event.id,
            conflict_type="value",
            severity="critical",
            status="open",
            summary="Profit forecast mismatch",
            claim_ids=[left.id, right.id],
        )
        repository.save_conflict(conflict)

        task = ReviewTask(
            id=new_id("rvt"),
            object_type="claim_conflict",
            object_id=conflict.id,
            reason_code="CONFLICT_VALUE",
            allowed_decisions=["approve", "reject"],
            status="pending",
        )
        repository.save_review_task(task)

        response = client.post(
            f"/api/v1/reviews/{task.id}/decision",
            headers={"Authorization": f"Bearer {token}"},
            json={"decision": "reject", "comment": "rejected as irreconcilable"},
        )
        assert response.status_code == 200

        updated_conflict = repository.get_conflict(conflict.id)
        assert updated_conflict is not None
        assert updated_conflict.status == "resolved"
        assert updated_conflict.resolution == "reject"

        updated_left = repository.get_claim(left.id)
        updated_right = repository.get_claim(right.id)
        assert updated_left is not None and updated_left.status == "rejected"
        assert updated_right is not None and updated_right.status == "rejected"
