"""事件影响分析 Agent。

从已验证事件事实出发，推理其对宏观经济、市场、板块、行业上下游和具体公司的
传导影响。输出严格经 ``ImpactAnalysisOutput`` Schema 校验；解析失败时服务层
降级为规则模板。
"""

from typing import Any

from app.analysis.schemas import ImpactAnalysisOutput, ImpactAnalysisOutputV2
from app.model_gateway.service import ModelGateway, ModelRequest

AGENT_SCHEMA_VERSION = "v2"


class ImpactAnalystAgent:
    """影响分析 Agent：基于事件上下文生成传导链与受影响目标。"""

    agent_type = "impact_analyst"
    operation = "impact_analysis"

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def analyze(
        self,
        event: Any,
        claims: list[Any],
        fact_card: Any | None,
        entities: list[Any],
        context: dict[str, Any] | None = None,
    ) -> ImpactAnalysisOutput | ImpactAnalysisOutputV2 | None:
        """调用 LLM；未配置 provider 或输出无法解析时返回 None（由服务层降级）。"""
        try:
            response = self.gateway.invoke(
                ModelRequest(
                    operation=self.operation,
                    input_schema_version=AGENT_SCHEMA_VERSION,
                    output_schema_version=AGENT_SCHEMA_VERSION,
                    payload=_build_payload(event, claims, fact_card, entities, context),
                    timeout_seconds=60,
                    system_prompt=_build_system_prompt(event),
                )
            )
        except Exception:
            return None

        payload = response.payload if isinstance(response.payload, dict) else {}
        try:
            output = ImpactAnalysisOutputV2.model_validate(payload)
        except Exception:
            try:
                output = ImpactAnalysisOutput.model_validate(payload)
            except Exception:
                return None
        output.model_run_id = response.run_id
        return output


def _build_payload(
    event: Any,
    claims: list[Any],
    fact_card: Any | None,
    entities: list[Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装给模型的结构化上下文。"""
    return {
        "event": {
            "id": event.id,
            "title": event.title,
            "event_type": event.event_type,
            "key_fields": event.key_fields,
            "importance": event.importance,
            "urgency": event.urgency,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        },
        "claims": [
            {
                "id": claim.id,
                "predicate": claim.predicate,
                "object_value": claim.object_value,
                "status": claim.status,
                "confidence": claim.confidence,
            }
            for claim in claims
        ],
        "fact_card": _fact_card_payload(fact_card) if fact_card else None,
        "entities": [
            {
                "id": entity.id,
                "entity_type": entity.entity_type,
                "canonical_name": entity.canonical_name,
            }
            for entity in entities
        ],
        "impact_context": context or {},
    }


def _fact_card_payload(fact_card: Any) -> dict[str, Any]:
    return {
        "id": fact_card.id,
        "version": fact_card.version,
        "summary": fact_card.summary,
        "content": fact_card.content,
    }


def _build_system_prompt(event: Any) -> str:
    return (
        "你是一位资深宏观与金融分析师。请基于下方提供的已验证事件事实，"
        "推理该事件对宏观经济、市场、板块、行业上下游和资产价格的传导影响。"
        "只使用输入中已验证的信息，不要编造未提供的实体或数据。"
        "输出必须是合法 JSON，不要包含 markdown 代码块或其他说明文字。\n\n"
        "优先输出 2.0.0 结构，extra fields 不允许：\n"
        "{\n"
        '  "schema_version": "2.0.0",\n'
        '  "summary": "用 2-4 句话概括核心影响逻辑（中文）",\n'
        '  "context_snapshot": {"expected_baseline": "unknown", "surprise": "unknown"},\n'
        '  "causal_graph": {"nodes": [{"node_id": "node_event", '
        '"node_type": "event", "label": "事件"}], "edges": []},\n'
        '  "scenarios": [{"scenario_id": "scn_base", "name": "base", '
        '"assumptions": ["无额外假设"], "active_edge_ids": [], '
        '"likelihood": "unknown"}],\n'
        '  "impact_assessments": [{"assessment_id": "ia_1", '
        '"scenario_id": "scn_base", "target_type": "sector", '
        '"target_name": "目标", "exposure_path": ["事件→目标"], '
        '"dimensions": [{"dimension": "other", '
        '"direction": "uncertain", "magnitude": "uncertain"}], '
        '"horizon": "unknown", "confidence": 0.0}],\n'
        '  "watch_items": ["后续值得跟踪的指标或事件"],\n'
        '  "quality_report": {"evidence_coverage": 0.0, '
        '"unresolved_references": [], "warnings": [], "blockers": []}\n'
        "}\n\n"
        "约束：\n"
        "- 事件类型为 \"" + event.event_type + "\"，请优先使用该领域的经典传导框架。\n"
        "- 每条因果边必须尽量绑定 evidence_refs，不能编造证据 ID。\n"
        "- 每个影响目标必须有 exposure_path；没有可靠数据时不得填写定量区间。\n"
        "- confidence 必须真实反映证据强度，不要全部为 0.9+。\n"
        "- 中文输出。"
    )
