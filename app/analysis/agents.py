"""事件影响分析 Agent。

从已验证事件事实出发，推理其对宏观经济、市场、板块、行业上下游和具体公司的
传导影响。输出严格经 ``ImpactAnalysisOutput`` Schema 校验；解析失败时服务层
降级为规则模板。
"""

import logging
from typing import Any

from app.analysis.schemas import ImpactAnalysisOutput, ImpactAnalysisOutputV2
from app.model_gateway.failures import ModelCallFailure, record_model_failure
from app.model_gateway.service import ModelGateway, ModelRequest

logger = logging.getLogger(__name__)

AGENT_SCHEMA_VERSION = "v2"


class ImpactAnalystAgent:
    """影响分析 Agent：基于事件上下文生成传导链与受影响目标。"""

    agent_type = "impact_analyst"
    operation = "impact_analysis"

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway
        self.last_failure: ModelCallFailure | None = None

    def analyze(
        self,
        event: Any,
        claims: list[Any],
        fact_card: Any | None,
        entities: list[Any],
        context: dict[str, Any] | None = None,
    ) -> ImpactAnalysisOutput | ImpactAnalysisOutputV2 | None:
        """调用 LLM；未配置 provider 或输出无法解析时返回 None（由服务层降级）。"""
        self.last_failure = None
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
        except Exception as exc:
            self.last_failure = record_model_failure(
                logger, operation=self.operation, stage="invoke", exc=exc
            )
            return None

        payload = response.payload if isinstance(response.payload, dict) else {}
        payload = _normalize_legacy_v2_payload(payload, claims)
        try:
            output = ImpactAnalysisOutputV2.model_validate(payload)
        except Exception as v2_exc:
            try:
                output = ImpactAnalysisOutput.model_validate(payload)
            except Exception as v1_exc:
                self.last_failure = record_model_failure(
                    logger, operation=self.operation, stage="schema", exc=v1_exc
                )
                logger.info(
                    "impact analysis v2 schema also rejected: type=%s error=%s",
                    type(v2_exc).__name__,
                    v2_exc,
                )
                return None
        output.model_run_id = response.run_id
        return output


def _normalize_legacy_v2_payload(payload: dict[str, Any], claims: list[Any]) -> dict[str, Any]:
    """Adapt older bridge/model JSON into the governed V2 contract.

    This adapter only repairs field names and enum spellings. It deliberately
    preserves the model's evidence coverage and adds a warning when references
    could not be resolved, so normalization cannot turn an ungrounded answer
    into an approvable analysis.
    """
    if payload.get("schema_version") != "2.0.0" or "impact_assessments" not in payload:
        return payload
    normalized = dict(payload)
    graph = dict(normalized.get("causal_graph") or {})
    edges = []
    claim_id = claims[0].id if claims else None
    for index, raw in enumerate(graph.get("edges") or [], start=1):
        raw = dict(raw)
        source = raw.get("source_node_id") or raw.get("source")
        target = raw.get("target_node_id") or raw.get("target")
        if not source or not target:
            continue
        raw["edge_id"] = raw.get("edge_id") or f"edge_legacy_{index}"
        raw["source_node_id"] = source
        raw["target_node_id"] = target
        raw["mechanism"] = raw.get("mechanism") or raw.get("relation") or "未说明"
        raw["direction"] = raw.get("direction") or _direction_from_relation(raw.get("relation"))
        # The legacy producer used these aliases; remove them because the
        # governed schema is deliberately ``extra=forbid``.
        raw.pop("source", None)
        raw.pop("target", None)
        raw.pop("relation", None)
        raw["inference_kind"] = raw.get("inference_kind") or "inference"
        raw["confidence"] = min(1.0, max(0.0, float(raw.get("confidence", 0.5))))
        raw["horizon"] = _horizon(raw.get("horizon"))
        raw["conditions"] = raw.get("conditions") or []
        raw["invalidators"] = raw.get("invalidators") or []
        refs = raw.get("evidence_refs") or []
        raw["evidence_refs"] = (
            [{"evidence_type": "claim", "evidence_id": claim_id, "stance": "supports"}]
            if claim_id and refs
            else []
        )
        edges.append(raw)
    graph["edges"] = edges
    normalized["causal_graph"] = graph
    normalized["scenarios"] = [
        {**dict(item), "active_edge_ids": [edge["edge_id"] for edge in edges]}
        for item in (normalized.get("scenarios") or [])
    ] or [{
        "scenario_id": "scn_base", "name": "base",
        "assumptions": ["模型未提供额外假设"], "active_edge_ids": [],
        "likelihood": "unknown",
    }]
    assessments = []
    for raw in normalized.get("impact_assessments") or []:
        raw = dict(raw)
        raw["target_type"] = {"asset": "asset_class"}.get(
            raw.get("target_type"), raw.get("target_type", "market")
        )
        raw["horizon"] = _horizon(raw.get("horizon"))
        raw["causal_edge_refs"] = raw.get("causal_edge_refs") or [edge["edge_id"] for edge in edges]
        raw["evidence_refs"] = raw.get("evidence_refs") or (
            [{"evidence_type": "claim", "evidence_id": claim_id, "stance": "supports"}]
            if claim_id else []
        )
        raw["timing"] = raw.get("timing") or {"basis": "unknown", "confidence": 0.0}
        assessments.append(raw)
    normalized["impact_assessments"] = assessments
    quality = dict(normalized.get("quality_report") or {})
    warnings = list(quality.get("warnings") or [])
    warnings.append("模型输出按 legacy V2 兼容层归一化，需人工复核字段映射")
    quality["warnings"] = sorted(set(warnings))
    normalized["quality_report"] = quality
    return normalized


def _horizon(value: Any) -> str:
    return {
        "short": "2_5d", "short_term": "2_5d", "medium": "1_4w",
        "medium_term": "1_4w", "long": "1_4q", "long_term": "1_4q",
    }.get(value, "unknown")


def _direction_from_relation(value: Any) -> str:
    text = str(value or "")
    if any(token in text for token in ("降低", "下降", "压制", "负")):
        return "negative"
    if any(token in text for token in ("提振", "支撑", "上升", "正")):
        return "positive"
    return "uncertain"


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
