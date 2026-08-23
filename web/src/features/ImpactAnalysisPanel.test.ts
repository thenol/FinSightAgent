import { describe, expect, it } from "vitest";
import { explainImpactForNonEconomist } from "./ImpactAnalysisPanel";
import {
  directionColor,
  edgeAnnotation,
  filterImpactGraph,
  type GraphFilter,
  type ImpactGraph,
} from "./ImpactGraphFlow";

const graph: ImpactGraph = {
  nodes: [
    {
      node_id: "node_event",
      node_type: "event",
      label: "美联储降息",
      layer: 0,
    },
    {
      node_id: "node_rate",
      node_type: "variable",
      label: "融资成本",
      layer: 1,
    },
    { node_id: "node_house", node_type: "impact", label: "房地产", layer: 4 },
    { node_id: "node_bank", node_type: "impact", label: "银行", layer: 4 },
  ],
  edges: [
    {
      edge_id: "edge_direct",
      source_node_id: "node_event",
      target_node_id: "node_rate",
      mechanism: "利率传导",
      direction: "positive",
      order: "direct",
      horizon: "0_1d",
      inference_kind: "fact",
      confidence: 0.8,
    },
    {
      edge_id: "edge_core",
      source_node_id: "node_rate",
      target_node_id: "node_house",
      mechanism: "融资传导",
      direction: "positive",
      order: "first_order",
      horizon: "1_4q",
      inference_kind: "inference",
      confidence: 0.7,
    },
    {
      edge_id: "edge_second",
      source_node_id: "node_rate",
      target_node_id: "node_bank",
      mechanism: "息差传导",
      direction: "negative",
      order: "second_order",
      horizon: "1_4q",
      inference_kind: "assumption",
      confidence: 0.5,
    },
  ],
};

const defaultFilter: GraphFilter = {
  scenarioId: "all",
  horizon: "all",
  minimumConfidence: 0.6,
  coreOnly: true,
};

describe("impact graph filters", () => {
  it("defaults to high-confidence direct and first-order paths", () => {
    const filtered = filterImpactGraph(graph, [], defaultFilter);
    expect(filtered.edges.map((edge) => edge.edge_id)).toEqual([
      "edge_direct",
      "edge_core",
    ]);
    expect(filtered.nodes.map((node) => node.node_id)).toEqual([
      "node_event",
      "node_rate",
      "node_house",
    ]);
  });

  it("reveals second-order edges when all paths and a lower threshold are selected", () => {
    const filtered = filterImpactGraph(graph, [], {
      ...defaultFilter,
      coreOnly: false,
      minimumConfidence: 0.5,
    });
    expect(filtered.edges).toHaveLength(3);
    expect(filtered.nodes.map((node) => node.node_id)).toContain("node_bank");
  });

  it("filters edges by selected scenario and time horizon", () => {
    const filtered = filterImpactGraph(
      graph,
      [
        {
          scenario_id: "scn_base",
          name: "base",
          active_edge_ids: ["edge_core"],
        },
      ],
      { ...defaultFilter, scenarioId: "scn_base", horizon: "1_4q" },
    );
    expect(filtered.edges.map((edge) => edge.edge_id)).toEqual(["edge_core"]);
  });
});

describe("directionColor", () => {
  it("maps directional semantics to stable graph colors", () => {
    expect(directionColor("positive")).toBe("#15803d");
    expect(directionColor("negative")).toBe("#dc2626");
    expect(directionColor("mixed")).toBe("#b45309");
    expect(directionColor("uncertain")).toBe("#64748b");
  });
});

describe("plain-language impact explanations", () => {
  it("explains direction, strength and timing without economic jargon", () => {
    const explanation = explainImpactForNonEconomist({
      target_type: "sector",
      target_name: "房地产",
      direction: "negative",
      magnitude: "strong",
      horizon: "medium",
      confidence: 0.8,
      rationale: "融资成本上升",
    });
    expect(explanation).toContain("行业板块“房地产”");
    expect(explanation).toContain("压制/不利");
    expect(explanation).toContain("明显影响");
    expect(explanation).toContain("不代表结果已经发生");
  });
});

describe("edge annotations", () => {
  it("maps structured causal fields to readable research labels", () => {
    const annotation = edgeAnnotation(graph.edges[1]);
    expect(annotation.directionLabel).toBe("正向");
    expect(annotation.orderLabel).toBe("一阶");
    expect(annotation.horizonLabel).toBe("1–4季度");
    expect(annotation.inferenceLabel).toBe("推断");
    expect(annotation.confidenceLabel).toBe("70%");
  });
});
