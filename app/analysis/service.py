"""事件影响分析服务。

负责版本化生成、持久化 ``ImpactAnalysis``，并在 LLM 不可用时提供规则降级模板。
"""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from app.analysis.agents import ImpactAnalystAgent
from app.analysis.schemas import ImpactAnalysisOutput, ImpactTarget, TransmissionChain
from app.domain import AuditLog, Event, ImpactAnalysis
from app.model_gateway.service import ModelGateway
from app.platform.ids import new_id
from app.platform.repository import Repository
from app.platform.settings import Settings

IMPACT_ANALYSIS_ACTOR = "agent:impact_analyst"


class ImpactAnalysisService:
    """生成并管理事件影响分析版本链。"""

    def __init__(
        self,
        repository: Repository,
        settings: Optional[Settings] = None,
        agent: Optional[ImpactAnalystAgent] = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or Settings.from_environment()
        self.agent = agent or ImpactAnalystAgent(ModelGateway(repository))

    def generate(self, event_id: str, actor: Optional[str] = None) -> ImpactAnalysis:
        """为事件生成新一版影响分析；LLM 失败时使用规则模板。"""
        event = self.repository.get_event(event_id)
        if event is None:
            raise ValueError(f"event not found: {event_id}")

        claims = self.repository.get_claims_for_event(event_id)
        fact_card = self.repository.get_fact_card_for_event(event_id)
        entities = [
            entity
            for entity_id in event.entity_ids
            if (entity := self.repository.get_entity(entity_id)) is not None
        ]

        output = self.agent.analyze(event, claims, fact_card, entities)
        degraded = output is None
        if degraded:
            output = _fallback_template(event)

        latest = self.repository.get_latest_impact_analysis_for_event(event_id)
        version = 1 if latest is None else latest.version + 1

        # 旧版本标记为 superseded
        if latest is not None:
            self.repository.update_impact_analysis(
                replace(latest, status="superseded")
            )

        analysis = ImpactAnalysis(
            id=new_id("imp"),
            event_id=event_id,
            version=version,
            status="approved",
            event_title_snapshot=event.title,
            summary=output.summary,
            transmission_chains=[chain.model_dump() for chain in output.transmission_chains],
            impacts=[impact.model_dump() for impact in output.impacts],
            macro_assumptions=list(output.macro_assumptions),
            watch_items=list(output.watch_items),
            generated_by=IMPACT_ANALYSIS_ACTOR,
            model_run_id=None if degraded else getattr(output, "model_run_id", None),
            degraded=degraded,
            supersedes_id=latest.id if latest else None,
            created_at=datetime.now(timezone.utc),
        )
        self.repository.save_impact_analysis(analysis)
        self._audit("impact_analysis.generated", analysis, actor or IMPACT_ANALYSIS_ACTOR)
        return analysis

    def get_latest(self, event_id: str) -> Optional[ImpactAnalysis]:
        return self.repository.get_latest_impact_analysis_for_event(event_id)

    def get(self, impact_analysis_id: str) -> Optional[ImpactAnalysis]:
        return self.repository.get_impact_analysis(impact_analysis_id)

    def list_versions(self, event_id: str, limit: Optional[int] = None) -> list[ImpactAnalysis]:
        return self.repository.list_impact_analyses_for_event(event_id, limit=limit)

    def _audit(self, action: str, analysis: ImpactAnalysis, actor: str) -> None:
        saver = getattr(self.repository, "save_audit_log", None)
        if not callable(saver):
            return
        saver(
            AuditLog(
                id=new_id("aud"),
                actor_id=actor,
                action=action,
                object_type="impact_analysis",
                object_id=analysis.id,
                request_id=None,
                details={
                    "event_id": analysis.event_id,
                    "version": analysis.version,
                    "degraded": analysis.degraded,
                    "model_run_id": analysis.model_run_id,
                    "impact_count": len(analysis.impacts),
                },
                created_at=datetime.now(timezone.utc),
            )
        )


def _fallback_template(event: Event) -> ImpactAnalysisOutput:
    """LLM 不可用时，按事件类型返回规则模板输出。"""
    if event.event_type == "macro_policy":
        return _macro_policy_fallback(event)
    return _generic_fallback(event)


def _macro_policy_fallback(event: Event) -> ImpactAnalysisOutput:
    key_fields = event.key_fields or {}
    rate_decision = key_fields.get("rate_decision", "unknown")
    policy_body = key_fields.get("policy_body", "central_bank")

    if rate_decision == "hike":
        summary = (
            f"{policy_body} 加息通常收紧流动性、推升无风险利率，"
            "对高估值成长股和高杠杆板块偏负面，对银行净息差偏正面。"
        )
        impacts = [
            ImpactTarget(
                target_type="sector",
                target_name="银行",
                direction="positive",
                magnitude="moderate",
                horizon="medium",
                confidence=0.65,
                rationale="加息往往扩大净息差，利好银行利息收入；但需关注资产质量恶化风险。",
            ),
            ImpactTarget(
                target_type="sector",
                target_name="房地产",
                direction="negative",
                magnitude="strong",
                horizon="medium",
                confidence=0.70,
                rationale="融资成本上升直接压制高杠杆地产开发商和按揭需求。",
            ),
            ImpactTarget(
                target_type="asset_class",
                target_name="成长股/高估值科技",
                direction="negative",
                magnitude="moderate",
                horizon="short",
                confidence=0.60,
                rationale="折现率上升压缩远期现金流估值。",
            ),
            ImpactTarget(
                target_type="macro_variable",
                target_name="汇率",
                direction="mixed",
                magnitude="moderate",
                horizon="short",
                confidence=0.55,
                rationale="利差扩大可能推升本币汇率，但取决于市场预期与资本流动。",
            ),
        ]
        steps = [
            {"step": 0, "description": f"{policy_body} 宣布加息"},
            {"step": 1, "description": "市场无风险利率与融资成本上升"},
            {"step": 2, "description": "高杠杆、远期现金流资产估值承压；银行息差受益"},
        ]
    elif rate_decision == "cut":
        summary = (
            f"{policy_body} 降息通常释放流动性、降低融资成本，"
            "利好风险资产和高杠杆板块，对银行净息差偏负面。"
        )
        impacts = [
            ImpactTarget(
                target_type="sector",
                target_name="房地产",
                direction="positive",
                magnitude="moderate",
                horizon="medium",
                confidence=0.65,
                rationale="融资成本下降刺激按揭和开发融资需求。",
            ),
            ImpactTarget(
                target_type="asset_class",
                target_name="成长股/高估值科技",
                direction="positive",
                magnitude="moderate",
                horizon="short",
                confidence=0.60,
                rationale="折现率下降提升远期现金流估值。",
            ),
            ImpactTarget(
                target_type="sector",
                target_name="银行",
                direction="negative",
                magnitude="moderate",
                horizon="medium",
                confidence=0.55,
                rationale="净息差可能收窄。",
            ),
        ]
        steps = [
            {"step": 0, "description": f"{policy_body} 宣布降息"},
            {"step": 1, "description": "市场利率与融资成本下降"},
            {"step": 2, "description": "风险资产估值修复，高杠杆行业受益"},
        ]
    else:
        summary = f"{policy_body} 维持利率不变，市场影响取决于政策声明措辞与预期差。"
        impacts = [
            ImpactTarget(
                target_type="market",
                target_name="整体市场",
                direction="neutral",
                magnitude="weak",
                horizon="short",
                confidence=0.50,
                rationale="利率维持不变通常不会直接改变资产定价，但政策沟通可能引发波动。",
            )
        ]
        steps = [
            {"step": 0, "description": f"{policy_body} 宣布维持利率不变"},
            {"step": 1, "description": "政策声明释放未来指引信号"},
            {"step": 2, "description": "资产价格按预期差调整"},
        ]

    return ImpactAnalysisOutput(
        summary=summary,
        transmission_chains=[
            TransmissionChain(
                chain_id="chn_fallback_rate",
                mechanism="利率传导",
                steps=steps,
                confidence=0.55,
            )
        ],
        impacts=impacts,
        macro_assumptions=[f"{policy_body} 政策信号被市场充分定价"],
        watch_items=["后续货币政策表态", "通胀与就业数据", "跨境资本流动"],
        confidence=0.55,
    )


def _generic_fallback(event: Event) -> ImpactAnalysisOutput:
    return ImpactAnalysisOutput(
        summary=f"事件 {event.title} 的影响分析暂不可用，待 LLM 配置就绪后重新生成。",
        transmission_chains=[],
        impacts=[
            ImpactTarget(
                target_type="market",
                target_name="相关市场",
                direction="mixed",
                magnitude="uncertain",
                horizon="uncertain",
                confidence=0.30,
                rationale="无足够结构化信息生成确定性影响。",
            )
        ],
        macro_assumptions=["事件影响需结合后续信息披露评估"],
        watch_items=["事件后续进展", "相关实体公告", "市场情绪指标"],
        confidence=0.30,
    )
