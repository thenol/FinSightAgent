"""事件影响分析 Agent。

从已验证事件事实出发，推理其对宏观经济、市场、板块、行业上下游和具体公司的
传导影响。输出严格经 ``ImpactAnalysisOutput`` Schema 校验；解析失败时服务层
降级为规则模板。
"""

from typing import Any

from app.analysis.schemas import ImpactAnalysisOutput
from app.model_gateway.service import ModelGateway, ModelRequest

AGENT_SCHEMA_VERSION = "v1"


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
    ) -> ImpactAnalysisOutput | None:
        """调用 LLM；未配置 provider 或输出无法解析时返回 None（由服务层降级）。"""
        try:
            response = self.gateway.invoke(
                ModelRequest(
                    operation=self.operation,
                    input_schema_version=AGENT_SCHEMA_VERSION,
                    output_schema_version=AGENT_SCHEMA_VERSION,
                    payload=_build_payload(event, claims, fact_card, entities),
                    timeout_seconds=60,
                    system_prompt=_build_system_prompt(event),
                )
            )
        except Exception:
            return None

        payload = response.payload if isinstance(response.payload, dict) else {}
        try:
            output = ImpactAnalysisOutput.model_validate(payload)
            output.model_run_id = response.run_id
            return output
        except Exception:
            return None


def _build_payload(
    event: Any,
    claims: list[Any],
    fact_card: Any | None,
    entities: list[Any],
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
        "输出需严格符合以下 JSON Schema（extra fields 不允许）：\n"
        "{\n"
        '  "schema_version": "1.0.0",\n'
        '  "summary": "用 2-4 句话概括核心影响逻辑（中文）",\n'
        '  "transmission_chains": [\n'
        "    {\n"
        '      "chain_id": "chn_前缀加小写英文/数字/下划线，如 chn_rate",\n'
        '      "mechanism": "传导机制名称（如 利率传导、流动性传导）",\n'
        '      "steps": [\n'
        "        {\"step\": 0, \"description\": \"第一步\"},\n"
        "        ...\n"
        "      ],\n"
        '      "confidence": 0.0-1.0\n'
        "    }\n"
        "  ],\n"
        '  "impacts": [\n'
        "    {\n"
        '      "target_type": "sector|industry|company|macro_variable|market|asset_class",\n'
        '      "target_name": "受影响对象中文名称",\n'
        '      "target_code": "可选标准化代码",\n'
        '      "direction": "positive|negative|neutral|mixed",\n'
        '      "magnitude": "strong|moderate|weak|uncertain",\n'
        '      "horizon": "short|medium|long|uncertain",\n'
        '      "confidence": 0.0-1.0,\n'
        '      "rationale": "简要依据（1-2 句）",\n'
        '      "chain_refs": ["chn_xxx"],\n'
        '      "claim_ids": []\n'
        "    }\n"
        "  ],\n"
        '  "macro_assumptions": ["分析依赖的宏观前提，如 市场已充分定价"],\n'
        '  "watch_items": ["后续值得跟踪的指标或事件"],\n'
        '  "confidence": 0.0-1.0\n'
        "}\n\n"
        "约束：\n"
        "- 事件类型为 \"" + event.event_type + "\"，请优先使用该领域的经典传导框架。\n"
        "- 至少给出 2 个、不超过 6 个 impacts。\n"
        "- 每个 impact 的 rationale 必须具体，不能是泛泛而谈。\n"
        "- confidence 必须真实反映证据强度，不要全部为 0.9+。\n"
        "- 中文输出。"
    )
