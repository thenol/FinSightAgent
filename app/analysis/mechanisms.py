"""影响分析因果机制生成与批评器。"""

from dataclasses import dataclass

from app.analysis.schemas import CausalGraph, ImpactAnalysisOutputV2


@dataclass(frozen=True)
class MechanismResult:
    graph: CausalGraph
    warnings: list[str]


@dataclass(frozen=True)
class ImpactCritique:
    blockers: list[str]
    warnings: list[str]


class MechanismGenerator:
    """规范化模型生成的机制图，并识别缺少证据的推理边。"""

    def generate(self, output: ImpactAnalysisOutputV2) -> MechanismResult:
        warnings: list[str] = []
        for edge in output.causal_graph.edges:
            if not edge.evidence_refs:
                warnings.append(f"edge_without_evidence:{edge.edge_id}")
            if edge.inference_kind == "assumption" and not edge.conditions:
                warnings.append(f"assumption_without_condition:{edge.edge_id}")
        return MechanismResult(graph=output.causal_graph, warnings=sorted(set(warnings)))


class ImpactCritic:
    """对因果图进行独立结构和推理一致性检查。"""

    def critique(self, output: ImpactAnalysisOutputV2) -> ImpactCritique:
        nodes = {node.node_id: node for node in output.causal_graph.nodes}
        blockers: list[str] = []
        warnings: list[str] = []
        event_nodes = [node for node in output.causal_graph.nodes if node.node_type == "event"]
        if not event_nodes:
            blockers.append("missing_event_node")
        for edge in output.causal_graph.edges:
            if edge.source_node_id not in nodes or edge.target_node_id not in nodes:
                blockers.append(f"edge_endpoint_missing:{edge.edge_id}")
            if edge.source_node_id == edge.target_node_id:
                blockers.append(f"self_loop:{edge.edge_id}")
            if edge.direction == "uncertain" and edge.confidence >= 0.8:
                warnings.append(f"high_confidence_uncertain_edge:{edge.edge_id}")
        for assessment in output.impact_assessments:
            if not any(
                node.node_type == "impact" and node.label == assessment.target_name
                for node in output.causal_graph.nodes
            ):
                warnings.append(f"impact_target_not_in_graph:{assessment.assessment_id}")
        return ImpactCritique(
            blockers=sorted(set(blockers)), warnings=sorted(set(warnings))
        )
