"""报告装配器。

ReportAssembler 从 Blackboard 投影报告草稿，不进行开放式生成（DD-60 §4）。
装配规则：
- 摘要中的数字和主体必须来自 ``verified`` Claim。
- ``conflicted`` Claim 只能进入冲突区，``unverified`` 只能进入待验证区。
- 核心判断必须关联 Company/Skeptic/Synthesis Analysis ID。
- ``fact_only`` 工作流只能生成事实卡片。
- 数据截止时间取 WorkflowRun ``as_of``，不得取发布时间或当前时间代替。
"""

from typing import Any

from app.domain import Claim, Event, WorkflowRun
from app.platform.repository import Repository

REPORT_SCHEMA_VERSION = "1.0.0"
DEFAULT_DISCLAIMER = "本内容由自动化系统生成，仅供研究参考，不构成投资建议。"

# 禁止在报告中出现的强措辞（GuardrailEngine 复用）
FORBIDDEN_PHRASES = (
    "买入",
    "卖出",
    "增持",
    "减持",
    "保证收益",
    "稳赚",
    "必涨",
    "必跌",
    "建议调仓",
    "自动交易",
    "全仓",
    "加杠杆",
)


class ReportAssembler:
    """从工作流 Blackboard 与已验证 Claim 投影报告草稿。"""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def assemble(self, run: WorkflowRun, event: Event) -> dict[str, Any]:
        blackboard = run.blackboard or {}
        claims = self.repository.get_claims_for_event(event.id, as_of=run.as_of)
        verified = [c for c in claims if c.status == "verified"]
        conflicted = [c for c in claims if c.status == "conflicted"]
        unverified = [c for c in claims if c.status == "unverified"]

        synthesis = blackboard.get("synthesis", {})
        company = blackboard.get("company_analysis", {})
        counter = blackboard.get("counter_analysis", {})
        preliminary = blackboard.get("preliminary_assessment", {})

        report_type = self._report_type(blackboard, synthesis)
        sections = self._sections(verified, conflicted, unverified, company, counter, synthesis)
        claim_ids = [c.id for c in verified]
        analysis_ids = self._analysis_ids(blackboard)
        confidence = self._confidence(synthesis, company, counter)

        summary = self._summary(event, verified, synthesis, report_type)
        signal = synthesis.get("signal") if report_type == "research_card" else None
        content = {
            "conclusion": synthesis.get("summary") or summary,
            "confidence": confidence,
            "time_range": {
                "as_of": run.as_of.isoformat(),
                "horizon": synthesis.get("horizon"),
            },
            "positive_viewpoints": company.get("financial_impacts", []),
            "negative_viewpoints": counter.get("counter_arguments", []),
            "watch_items": synthesis.get("watch_items", []),
            "reanalysis_conditions": synthesis.get("reanalysis_triggers", []),
        }
        preliminary_id = blackboard.get("preliminary_assessment_ref") or preliminary.get("id")
        if preliminary_id:
            content["preliminary_assessment"] = {
                "id": preliminary_id,
                "thesis": preliminary.get("thesis"),
                "direction": preliminary.get("direction"),
                "disposition": synthesis.get("assessment_disposition"),
                "delta": synthesis.get("assessment_delta", {}),
                "delta_reasons": synthesis.get("delta_reasons", []),
            }
        tool_calls = self.repository.list_tool_calls(run.id)
        provenance = {
            "workflow_run_id": run.id,
            "analysis_ids": analysis_ids,
            "analysis_refs": self._analysis_refs(blackboard),
            "model_run_ids": self._model_run_ids(blackboard),
            "tool_call_ids": [call.id for call in tool_calls],
            "preliminary_assessment_id": preliminary_id,
        }

        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "report_type": report_type,
            "event_id": event.id,
            "as_of": run.as_of.isoformat(),
            "title": event.title,
            "summary": summary,
            "signal": signal,
            "confidence": confidence,
            "claim_ids": claim_ids,
            "analysis_ids": analysis_ids,
            "sections": sections,
            "disclaimer": DEFAULT_DISCLAIMER,
            "content": content,
            "provenance": provenance,
        }

    def _report_type(self, blackboard: dict, synthesis: dict) -> str:
        # fact_only 工作流（缺关键分析或 synthesis 显式标记）只生成事实卡片
        if synthesis.get("status") == "fact_only":
            return "fact_card"
        if not blackboard.get("company_analysis") or not blackboard.get("synthesis"):
            return "fact_card"
        return "research_card"

    def _sections(
        self,
        verified: list[Claim],
        conflicted: list[Claim],
        unverified: list[Claim],
        company: dict,
        counter: dict,
        synthesis: dict,
    ) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = [
            {
                "kind": "verified_facts",
                "title": "已验证事实",
                "items": [
                    {
                        "claim_id": c.id,
                        "subject": c.subject_text,
                        "predicate": c.predicate,
                        "value": c.object_value,
                    }
                    for c in verified
                ],
            }
        ]
        if company:
            sections.append(
                {
                    "kind": "impact",
                    "title": "公司影响",
                    "items": company.get("financial_impacts", []),
                }
            )
        if counter:
            sections.append(
                {
                    "kind": "counter_arguments",
                    "title": "反方观点",
                    "items": counter.get("counter_arguments", []),
                }
            )
        if conflicted:
            sections.append(
                {
                    "kind": "conflicts",
                    "title": "冲突说明",
                    "items": [{"claim_id": c.id, "subject": c.subject_text} for c in conflicted],
                }
            )
        if unverified:
            sections.append(
                {
                    "kind": "unverified",
                    "title": "待验证事项",
                    "items": [{"claim_id": c.id, "subject": c.subject_text} for c in unverified],
                }
            )
        if synthesis.get("watch_items"):
            sections.append(
                {
                    "kind": "watch_items",
                    "title": "后续观察",
                    "items": synthesis["watch_items"],
                }
            )
        if synthesis.get("reanalysis_triggers"):
            sections.append(
                {
                    "kind": "triggers",
                    "title": "重新分析条件",
                    "items": synthesis["reanalysis_triggers"],
                }
            )
        if synthesis.get("limitations"):
            sections.append(
                {
                    "kind": "limitations",
                    "title": "局限性",
                    "items": [{"statement": s} for s in synthesis["limitations"]],
                }
            )
        return sections

    def _analysis_ids(self, blackboard: dict) -> list[str]:
        ids = []
        for key in ("company_analysis", "counter_analysis", "synthesis"):
            block = blackboard.get(key)
            if block and block.get("model_run_id"):
                ids.append(block["model_run_id"])
        # 回退：用 Blackboard 字段名作为分析引用
        if not ids:
            ids = [
                k
                for k in ("company_analysis", "counter_analysis", "synthesis")
                if blackboard.get(k)
            ]
        return ids

    def _model_run_ids(self, blackboard: dict) -> list[str]:
        ids = self._block_metadata(blackboard, "model_run_id")
        if ids:
            return ids
        # Older persisted Blackboards may only carry the workflow-level aggregate.
        provenance = blackboard.get("provenance")
        if not isinstance(provenance, dict):
            return []
        return self._unique_strings(provenance.get("model_run_ids"))

    def _analysis_refs(self, blackboard: dict) -> list[str]:
        refs = self._block_metadata(blackboard, "analysis_ref")
        if refs:
            return refs
        provenance = blackboard.get("provenance")
        if not isinstance(provenance, dict):
            return []
        return self._unique_strings(provenance.get("analysis_refs"))

    def _block_metadata(self, blackboard: dict, field: str) -> list[str]:
        values = []
        for key in (
            "fact_check_snapshot",
            "company_analysis",
            "counter_analysis",
            "synthesis",
        ):
            block = blackboard.get(key)
            if isinstance(block, dict):
                values.append(block.get(field))
        return self._unique_strings(values)

    @staticmethod
    def _unique_strings(values: Any) -> list[str]:
        if not isinstance(values, (list, tuple)):
            return []
        result: list[str] = []
        for value in values:
            if isinstance(value, str) and value and value not in result:
                result.append(value)
        return result

    def _confidence(self, synthesis: dict, company: dict, counter: dict) -> float:
        base = synthesis.get("confidence")
        if base is not None:
            return float(base)
        company_conf = company.get("confidence", 0.4)
        skeptic_conf = counter.get("recommended_confidence", 0.5)
        return round(min(float(company_conf), float(skeptic_conf)), 3)

    def _summary(
        self, event: Event, verified: list[Claim], synthesis: dict, report_type: str
    ) -> str:
        if report_type == "fact_card":
            return f"基于 {len(verified)} 条已验证事实生成事实卡片。{event.title}"
        return synthesis.get("summary") or f"{event.title}：{synthesis.get('signal', 'uncertain')}"
