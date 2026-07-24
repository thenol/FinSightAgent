from datetime import datetime, timezone

from app.domain import Claim, Event
from app.platform.ids import new_id
from app.platform.repository import InMemoryRepository
from app.workflows.service import WorkflowService


def _seed_event_with_claim(repository: InMemoryRepository) -> str:
    repository.save_event(
        Event(
            id="evt_idem",
            event_type="earnings_guidance",
            status="triaged",
            title="测试幂等",
            entity_ids=[],
            document_ids=[],
            importance=0.5,
            urgency="normal",
            occurred_at=datetime.now(timezone.utc),
        )
    )
    repository.save_claim(
        Claim(
            id=new_id("clm"),
            event_id="evt_idem",
            subject_text="000001.SZ",
            predicate="document_discloses_event",
            object_value={"type": "string", "value": "earnings_guidance"},
            status="verified",
            confidence=0.9,
            evidence_ids=[],
            as_of=datetime.now(timezone.utc),
        )
    )
    return "evt_idem"


def test_workflow_records_node_attempts_for_each_node() -> None:
    repository = InMemoryRepository()
    _seed_event_with_claim(repository)
    service = WorkflowService(repository)
    run = service.create("evt_idem", "manual")
    service.run(run.id)

    attempts = [a for a in repository.node_attempts if a.workflow_id == run.id]
    # 7 个节点都应留下成功 attempt
    succeeded = [a for a in attempts if a.status == "succeeded"]
    assert len(succeeded) == 7
    node_names = {a.node_name for a in succeeded}
    assert node_names == {
        "context",
        "fact_check",
        "company",
        "skeptic",
        "synthesize",
        "guardrail",
        "draft",
    }


def test_replay_reuses_node_attempt_without_duplicate_side_effects() -> None:
    """重放同输入命中幂等，不重复写 NodeAttempt、不重复模型调用。"""
    repository = InMemoryRepository()
    _seed_event_with_claim(repository)
    service = WorkflowService(repository)
    run = service.create("evt_idem", "manual")
    first = service.run(run.id)

    first_model_runs = len(repository.model_runs)
    first_attempts = len(repository.node_attempts)

    # 重跑同一 workflow（LangGraph 从检查点恢复；幂等命中已成功节点）
    second = service.run(run.id)

    # NodeAttempt 不应重复增加（幂等命中复用）
    assert len(repository.node_attempts) == first_attempts
    # 模型调用也不重复
    assert len(repository.model_runs) == first_model_runs
    # 结果一致
    assert second.status == first.status


def test_node_attempt_records_input_hash() -> None:
    repository = InMemoryRepository()
    _seed_event_with_claim(repository)
    service = WorkflowService(repository)
    run = service.create("evt_idem", "manual")
    service.run(run.id)

    attempts = [
        a for a in repository.node_attempts if a.workflow_id == run.id and a.status == "succeeded"
    ]
    # input_hash 非空且为 64 字符 sha256
    for attempt in attempts:
        assert len(attempt.input_hash) == 64
    # company 节点的 input_hash 基于其读取的字段
    company_attempt = next(a for a in attempts if a.node_name == "company")
    assert company_attempt.output is not None
    assert "company_analysis" in company_attempt.output
