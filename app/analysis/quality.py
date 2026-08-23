"""影响分析结构化质量门禁。"""

from app.analysis.schemas import ImpactAnalysisOutputV2


def validate_impact_output(output: ImpactAnalysisOutputV2) -> list[str]:
    """返回阻断项；空列表表示结构与引用满足发布前门禁。"""
    blockers = list(output.quality_report.blockers)
    node_ids = {node.node_id for node in output.causal_graph.nodes}
    edge_ids = {edge.edge_id for edge in output.causal_graph.edges}
    scenario_ids = {scenario.scenario_id for scenario in output.scenarios}

    for edge in output.causal_graph.edges:
        if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
            blockers.append(f"dangling causal edge: {edge.edge_id}")
    for scenario in output.scenarios:
        missing = sorted(set(scenario.active_edge_ids) - edge_ids)
        if missing:
            blockers.append(f"scenario {scenario.scenario_id} references missing edges: {missing}")
    for assessment in output.impact_assessments:
        if assessment.scenario_id not in scenario_ids:
            blockers.append(f"assessment {assessment.assessment_id} references missing scenario")
        if not assessment.exposure_path:
            blockers.append(f"assessment {assessment.assessment_id} has no exposure path")
        for dimension in assessment.dimensions:
            if dimension.quantitative_range and not assessment.evidence_refs:
                blockers.append(
                    "quantitative assessment without evidence: "
                    f"{assessment.assessment_id}"
                )
    if output.quality_report.evidence_coverage < 0.95:
        blockers.append("evidence coverage below 0.95")
    return sorted(set(blockers))
