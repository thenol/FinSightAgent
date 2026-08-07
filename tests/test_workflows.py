from datetime import datetime, timezone

from app.domain import Event
from app.platform.repository import InMemoryRepository
from app.workflows.service import WorkflowService


def test_langgraph_research_workflow_blocked_when_guardrail_fails() -> None:
    repository = InMemoryRepository()
    repository.save_event(
        Event(
            id="evt-workflow",
            event_type="earnings_guidance",
            status="triaged",
            title="Test",
            entity_ids=[],
            document_ids=[],
            importance=0.5,
            urgency="normal",
            occurred_at=datetime.now(timezone.utc),
        )
    )
    service = WorkflowService(repository)
    run = service.create("evt-workflow", "manual")
    result = service.run(run.id)
    assert result.status == "failed"
    assert result.error_code.startswith("GUARDRAIL_")
    # draft 已装配，只是未通过校验
    assert result.blackboard["report_draft_ref"] == "workflow:evt-workflow"


def test_workflow_fills_blackboard_with_agent_outputs() -> None:
    # 预置一条已验证 Claim 及其 Evidence，使 fact_check/company/guardrail 有事实可引用
    from app.domain import Claim, EvidenceSpan
    from app.platform.ids import new_id

    repository = InMemoryRepository()
    repository.save_event(
        Event(
            id="evt-bb",
            event_type="earnings_guidance",
            status="triaged",
            title="测试",
            entity_ids=[],
            document_ids=[],
            importance=0.5,
            urgency="normal",
            occurred_at=datetime.now(timezone.utc),
        )
    )
    evidence_id = new_id("evd")
    repository.save_evidence(
        EvidenceSpan(
            id=evidence_id,
            document_id="doc_1",
            revision_id="rev_1",
            locator={"type": "html", "block_id": "body-p-001", "char_start": 0, "char_end": 10},
            excerpt="测试证据",
            excerpt_hash="hash",
            locator_type="html",
            extraction_method="parser",
            extraction_version="html-blocks-v1",
            created_at=datetime.now(timezone.utc),
        )
    )
    repository.save_claim(
        Claim(
            id=new_id("clm"),
            event_id="evt-bb",
            subject_text="000001.SZ",
            predicate="document_discloses_event",
            object_value={"type": "string", "value": "earnings_guidance"},
            status="verified",
            confidence=0.9,
            evidence_ids=[evidence_id],
            as_of=datetime.now(timezone.utc),
        )
    )
    service = WorkflowService(repository)
    run = service.create("evt-bb", "manual")
    result = service.run(run.id)

    bb = result.blackboard
    # 四个 Agent 节点都写入了 Blackboard 字段（不再是 lambda 占位）
    assert "fact_check_snapshot" in bb
    assert "company_analysis" in bb
    assert "counter_analysis" in bb
    assert "synthesis" in bb
    assert bb["company_analysis"]["direction"] == "positive"
    assert bb["synthesis"]["schema_version"] == "1.0.0"
    # Guardrail 校验通过（存在已验证事实）
    assert bb["guardrail_result"]["passed"] is True
