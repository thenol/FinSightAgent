"""事件级初步研判：在事实核验后保存 Agent 的可解释研究假设。"""

import hashlib
import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from app.analysis.context import ImpactContextBuilder
from app.analysis.schemas import PreliminaryAssessmentOutputV1
from app.domain import AuditLog, Event, EventPreliminaryAssessment
from app.model_gateway.failures import record_model_failure
from app.model_gateway.service import ModelGateway, ModelRequest
from app.platform.ids import new_id

logger = logging.getLogger(__name__)


class PreliminaryAssessmentAgent:
    agent_type = "preliminary_assessor"
    operation = "preliminary_assessment"
    version = "1.0.0"
    prompt_version = "preliminary-assessment-v1"

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway
        self.last_failure: Any = None

    def analyze(
        self, event: Event, context: dict[str, Any]
    ) -> PreliminaryAssessmentOutputV1 | None:
        self.last_failure = None
        try:
            response = self.gateway.invoke(
                ModelRequest(
                    operation=self.operation,
                    input_schema_version="v1",
                    output_schema_version="preliminary-assessment/1.0.0",
                    payload={"event": _event_payload(event), "context": context},
                    timeout_seconds=60,
                    system_prompt=(
                        "你是事件级金融研究员。请在正式结论前，对整个事件做一份可审计的初步研判。"
                        "严格区分已验证事实、推理假设和未知事项；"
                        "所有事实必须引用输入中的 evidence_refs。"
                        "不要给出保证收益或交易指令。只输出合法 JSON，不要 markdown。\n"
                        "字段：summary, thesis, direction, significance, confidence, "
                        "event_characterization, affected_scope, mechanism_hypotheses, "
                        "scenario_outline, counter_hypotheses, uncertainties, missing_data, "
                        "watch_items, evidence_refs。"
                    ),
                )
            )
            return PreliminaryAssessmentOutputV1.model_validate(response.payload)
        except Exception as exc:
            self.last_failure = record_model_failure(
                logger, operation=self.operation, stage="invoke", exc=exc
            )
            return None


class PreliminaryAssessmentService:
    def __init__(self, repository, gateway: ModelGateway | None = None) -> None:
        self.repository = repository
        self.context_builder = ImpactContextBuilder(repository)
        self.agent = PreliminaryAssessmentAgent(gateway or ModelGateway(repository))

    def generate(
        self,
        event_id: str,
        *,
        workflow_id: str | None = None,
        actor: str = "agent:preliminary_assessor",
    ) -> EventPreliminaryAssessment:
        event = self.repository.get_event(event_id)
        if event is None:
            raise ValueError(f"event not found: {event_id}")
        context = self.context_builder.build(event)
        input_snapshot = context.to_payload()
        input_snapshot["event"] = _event_payload(event)
        input_hash = hashlib.sha256(
            json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        for item in self.repository.list_preliminary_assessments_for_event(event_id):
            if item.input_hash == input_hash and item.status != "superseded":
                return item
        output = self.agent.analyze(event, input_snapshot)
        degraded = output is None
        if output is None:
            output = _fallback(event, context)
        response = output.model_dump(mode="json")
        quality = {
            "gate_passed": not degraded and bool(context.claims),
            "evidence_coverage": min(1.0, len(context.claims) / 3) if context.claims else 0.0,
            "blockers": (["model_unavailable"] if degraded else [])
            + (["no_verified_claims"] if not context.claims else []),
            "warnings": context.warnings,
        }
        versions = self.repository.list_preliminary_assessments_for_event(event_id)
        previous = max(versions, key=lambda item: item.version, default=None)
        version = (previous.version + 1) if previous else 1
        assessment = EventPreliminaryAssessment(
            id=new_id("pea"),
            event_id=event_id,
            workflow_id=workflow_id,
            version=version,
            status="limited" if quality["blockers"] else "ready",
            event_title_snapshot=event.title,
            as_of=context.as_of,
            summary=output.summary,
            thesis=output.thesis,
            direction=output.direction,
            significance=output.significance,
            confidence=output.confidence,
            assessment_payload=response,
            input_snapshot=input_snapshot,
            input_hash=input_hash,
            quality_report=quality,
            generated_by=actor,
            model_run_id=None,
            agent_version=self.agent.version,
            prompt_version=self.agent.prompt_version,
            supersedes_id=previous.id if previous else None,
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_preliminary_assessment(assessment)
        if previous is not None:
            self.repository.update_preliminary_assessment(replace(previous, status="superseded"))
        saver = getattr(self.repository, "save_audit_log", None)
        if callable(saver):
            saver(
                AuditLog(
                    id=new_id("aud"),
                    actor_id=actor,
                    action="preliminary_assessment.generated",
                    object_type="event_preliminary_assessment",
                    object_id=assessment.id,
                    request_id=None,
                    details={
                        "event_id": event_id,
                        "version": version,
                        "status": assessment.status,
                        "input_hash": input_hash,
                    },
                    created_at=datetime.now(timezone.utc),
                )
            )
        return assessment


def _event_payload(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "event_type": event.event_type,
        "key_fields": event.key_fields,
        "importance": event.importance,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
    }


def _fallback(event: Event, context) -> PreliminaryAssessmentOutputV1:
    claim_ids = [claim.id for claim in context.claims]
    return PreliminaryAssessmentOutputV1(
        summary=f"事件“{event.title}”已完成初步结构化研判，但当前证据不足以形成稳定方向结论。",
        thesis="先确认事件事实、受影响主体和市场预期差异，再判断其持续性影响。",
        direction="uncertain",
        significance="high" if event.importance >= 0.7 else "medium",
        confidence=0.35 if claim_ids else 0.2,
        event_characterization={
            "what_happened": event.title,
            "data_cutoff": context.as_of.isoformat(),
        },
        uncertainties=["缺少可用于验证市场预期和价格反应的完整数据"],
        missing_data=context.warnings or ["verified_claims"],
        watch_items=["后续正式披露、独立来源确认和相关资产价格反应"],
        evidence_refs=[
            {
                "evidence_type": "claim",
                "evidence_id": item,
                "stance": "supports",
                "as_of": context.as_of.isoformat(),
            }
            for item in claim_ids
        ],
    )
