import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";
import { apiDelete, apiGet, apiPut } from "@/lib/api";
import "@xyflow/react/dist/style.css";

export type GraphEvidence = {
  evidence_type: string;
  evidence_id: string;
  stance?: string;
};

export type ImpactGraphNode = {
  node_id: string;
  node_type: "event" | "mechanism" | "variable" | "entity" | "impact" | string;
  label: string;
  layer?: number;
  group?: string | null;
};

export type ImpactGraphEdge = {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
  mechanism: string;
  direction: string;
  order?: string;
  horizon?: string;
  inference_kind?: string;
  confidence: number;
  conditions?: string[];
  invalidators?: string[];
  evidence_refs?: GraphEvidence[];
};

export type ImpactGraph = {
  nodes: ImpactGraphNode[];
  edges: ImpactGraphEdge[];
};
export type GraphScenario = {
  scenario_id: string;
  name: string;
  active_edge_ids: string[];
};

export type GraphFilter = {
  scenarioId: string;
  horizon: string;
  minimumConfidence: number;
  coreOnly: boolean;
};

export type EdgeAnnotation = {
  directionLabel: string;
  orderLabel: string;
  horizonLabel: string;
  inferenceLabel: string;
  confidenceLabel: string;
  directionSymbol: string;
};

type LayoutSnapshot = {
  node_positions: Record<string, { x: number; y: number }>;
  collapsed_groups: string[];
  viewport: Record<string, number>;
};

type GraphNodeData = ImpactGraphNode & {
  connected: boolean;
  dimmed: boolean;
  incomingCount: number;
  outgoingCount: number;
};

const elk = new ELK();
const nodeTypes = { researchNode: ResearchNode };
const nodeLabels: Record<string, string> = {
  event: "事件",
  mechanism: "传导机制",
  variable: "关键变量",
  entity: "主体",
  impact: "影响对象",
};
const directionLabels: Record<string, string> = {
  positive: "正向",
  negative: "负向",
  mixed: "混合",
  uncertain: "不确定",
};
const orderLabels: Record<string, string> = {
  direct: "直接",
  first_order: "一阶",
  second_order: "二阶",
};
const horizonLabels: Record<string, string> = {
  "0_1d": "0–1日",
  "2_5d": "2–5日",
  "1_4w": "1–4周",
  "1_4q": "1–4季度",
  "1y_plus": "1年以上",
  unknown: "时滞未知",
};
const inferenceLabels: Record<string, string> = {
  fact: "事实",
  derived: "推导",
  analogue: "类比",
  inference: "推断",
  assumption: "假设",
};

export function edgeAnnotation(edge: ImpactGraphEdge): EdgeAnnotation {
  return {
    directionLabel: directionLabels[edge.direction] ?? "不确定",
    orderLabel: orderLabels[edge.order ?? "direct"] ?? "传导",
    horizonLabel: horizonLabels[edge.horizon ?? "unknown"] ?? "时滞未知",
    inferenceLabel:
      inferenceLabels[edge.inference_kind ?? "inference"] ?? "推断",
    confidenceLabel: `${Math.round(edge.confidence * 100)}%`,
    directionSymbol:
      edge.direction === "positive"
        ? "+"
        : edge.direction === "negative"
          ? "−"
          : "±",
  };
}

function edgeLabel(
  edge: ImpactGraphEdge,
  annotationMode: "compact" | "full",
): string {
  const annotation = edgeAnnotation(edge);
  if (annotationMode === "full") {
    return `${edge.mechanism} · ${annotation.directionSymbol} ${annotation.confidenceLabel} · ${annotation.orderLabel} · ${annotation.horizonLabel}`;
  }
  return `${edge.mechanism} · ${annotation.directionSymbol} ${annotation.confidenceLabel}`;
}

export function directionColor(direction: string): string {
  if (direction === "positive") return "#15803d";
  if (direction === "negative") return "#dc2626";
  if (direction === "mixed") return "#b45309";
  return "#64748b";
}

function nodeTypeColor(nodeType: string): string {
  return (
    {
      event: "#2563eb",
      mechanism: "#7c3aed",
      variable: "#0891b2",
      entity: "#b45309",
      impact: "#dc2626",
    }[nodeType] ?? "#64748b"
  );
}

export function filterImpactGraph(
  graph: ImpactGraph,
  scenarios: GraphScenario[],
  filter: GraphFilter,
): ImpactGraph {
  let edges = graph.edges.filter(
    (edge) => edge.confidence >= filter.minimumConfidence,
  );
  if (filter.coreOnly) {
    edges = edges.filter(
      (edge) => edge.order === "direct" || edge.order === "first_order",
    );
  }
  if (filter.horizon !== "all")
    edges = edges.filter((edge) => edge.horizon === filter.horizon);
  if (filter.scenarioId !== "all") {
    const selected = scenarios.find(
      (scenario) => scenario.scenario_id === filter.scenarioId,
    );
    if (selected) {
      const active = new Set(selected.active_edge_ids);
      edges = edges.filter((edge) => active.has(edge.edge_id));
    }
  }
  const visibleIds = new Set(
    edges.flatMap((edge) => [edge.source_node_id, edge.target_node_id]),
  );
  const eventNodes = graph.nodes.filter((node) => node.node_type === "event");
  return {
    nodes: graph.nodes.filter(
      (node) => visibleIds.has(node.node_id) || eventNodes.includes(node),
    ),
    edges,
  };
}

function ResearchNode({ data, selected }: NodeProps<Node<GraphNodeData>>) {
  const item = data as GraphNodeData;
  return (
    <div
      className={`impact-graph-node impact-graph-node--${item.node_type} ${item.dimmed ? "is-dimmed" : ""} ${selected ? "is-selected" : ""}`}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="impact-graph-handle"
      />
      <div className="impact-graph-node__eyebrow">
        {nodeLabels[item.node_type] ?? item.node_type}
      </div>
      <div className="impact-graph-node__label">{item.label}</div>
      <div className="impact-graph-node__meta">
        <span>{item.group ?? "因果节点"}</span>
        <span>
          {item.incomingCount} 入 · {item.outgoingCount} 出
        </span>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="impact-graph-handle"
      />
    </div>
  );
}

function relatedNodeIds(
  graph: ImpactGraph,
  nodeId: string | null,
): Set<string> {
  if (!nodeId) return new Set(graph.nodes.map((node) => node.node_id));
  const visible = new Set([nodeId]);
  const queue = [nodeId];
  while (queue.length) {
    const current = queue.shift();
    for (const edge of graph.edges) {
      if (edge.source_node_id !== current && edge.target_node_id !== current)
        continue;
      const next =
        edge.source_node_id === current
          ? edge.target_node_id
          : edge.source_node_id;
      if (!visible.has(next)) {
        visible.add(next);
        queue.push(next);
      }
    }
  }
  return visible;
}

async function layoutGraph(
  graph: ImpactGraph,
): Promise<Record<string, { x: number; y: number }>> {
  const layout = await elk.layout({
    id: "impact-root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.spacing.nodeNodeBetweenLayers": "104",
      "elk.spacing.nodeNode": "34",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
    },
    children: graph.nodes.map((node) => ({
      id: node.node_id,
      width: 228,
      height: 102,
    })),
    edges: graph.edges.map((edge) => ({
      id: edge.edge_id,
      sources: [edge.source_node_id],
      targets: [edge.target_node_id],
    })),
  });
  return Object.fromEntries(
    (layout.children ?? []).map((node) => [
      node.id,
      { x: node.x ?? 0, y: node.y ?? 0 },
    ]),
  );
}

export function ImpactGraphFlow({
  analysisId,
  graph,
  scenarios,
  legacy,
  filter,
  onFilterChange,
  readOnly = false,
}: {
  analysisId: string;
  graph: ImpactGraph;
  scenarios: GraphScenario[];
  legacy: boolean;
  filter: GraphFilter;
  onFilterChange: (filter: GraphFilter) => void;
  readOnly?: boolean;
}) {
  const filtered = useMemo(
    () => filterImpactGraph(graph, scenarios, filter),
    [filter, graph, scenarios],
  );
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<ImpactGraphEdge | null>(
    null,
  );
  const [selectedNode, setSelectedNode] = useState<ImpactGraphNode | null>(
    null,
  );
  const [annotationMode, setAnnotationMode] = useState<"compact" | "full">(
    "compact",
  );
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [layoutNonce, setLayoutNonce] = useState(0);
  const [layoutState, setLayoutState] = useState<"idle" | "saving" | "saved">(
    "idle",
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<GraphNodeData>>(
    [],
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<ImpactGraphEdge>>(
    [],
  );
  const viewportRef = useRef<Record<string, number>>({});
  const saveTimer = useRef<number | undefined>(undefined);

  const persistLayout = useCallback(
    (nextNodes: Node<GraphNodeData>[]) => {
      if (readOnly) return;
      window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        setLayoutState("saving");
        void apiPut<LayoutSnapshot>(
          `/api/v1/impact-analyses/${encodeURIComponent(analysisId)}/layout`,
          {
            node_positions: Object.fromEntries(
              nextNodes.map((node) => [node.id, node.position]),
            ),
            collapsed_groups: [],
            viewport: viewportRef.current,
          },
        )
          .then(() => setLayoutState("saved"))
          .catch(() => setLayoutState("idle"));
      }, 450);
    },
    [analysisId, readOnly],
  );

  useEffect(() => () => window.clearTimeout(saveTimer.current), []);

  useEffect(() => {
    let cancelled = false;
    const initialise = async () => {
      const [autoPositions, saved] = await Promise.all([
        layoutGraph(filtered),
        readOnly
          ? Promise.resolve<LayoutSnapshot | null>(null)
          : apiGet<LayoutSnapshot>(
              `/api/v1/impact-analyses/${encodeURIComponent(analysisId)}/layout`,
            ).catch(() => null),
      ]);
      if (cancelled) return;
      const related = relatedNodeIds(filtered, focusNodeId);
      const incoming = new Map<string, number>();
      const outgoing = new Map<string, number>();
      filtered.edges.forEach((edge) => {
        incoming.set(
          edge.target_node_id,
          (incoming.get(edge.target_node_id) ?? 0) + 1,
        );
        outgoing.set(
          edge.source_node_id,
          (outgoing.get(edge.source_node_id) ?? 0) + 1,
        );
      });
      setNodes(
        filtered.nodes.map((node) => ({
          id: node.node_id,
          type: "researchNode",
          position: saved?.node_positions[node.node_id] ??
            autoPositions[node.node_id] ?? { x: 0, y: 0 },
          data: {
            ...node,
            connected: related.has(node.node_id),
            dimmed: !related.has(node.node_id),
            incomingCount: incoming.get(node.node_id) ?? 0,
            outgoingCount: outgoing.get(node.node_id) ?? 0,
          },
        })),
      );
      setEdges(
        filtered.edges.map((edge) => ({
          id: edge.edge_id,
          source: edge.source_node_id,
          target: edge.target_node_id,
          type: "smoothstep",
          label: edgeLabel(edge, annotationMode),
          labelStyle: { fill: "#475569", fontSize: 10, fontWeight: 600 },
          labelBgStyle: { fill: "#ffffff", fillOpacity: 0.92 },
          labelBgPadding: [4, 3],
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: directionColor(edge.direction),
            width: 16,
            height: 16,
          },
          animated: edge.order === "direct",
          style: {
            stroke: directionColor(edge.direction),
            strokeWidth: Math.max(1.4, edge.confidence * 4),
            strokeOpacity:
              related.has(edge.source_node_id) &&
              related.has(edge.target_node_id)
                ? 0.92
                : 0.14,
            strokeDasharray:
              edge.inference_kind === "fact"
                ? undefined
                : edge.inference_kind === "assumption"
                  ? "6 5"
                  : "3 3",
          },
          data: edge,
        })),
      );
    };
    void initialise();
    return () => {
      cancelled = true;
    };
  }, [
    analysisId,
    annotationMode,
    filtered,
    focusNodeId,
    layoutNonce,
    readOnly,
    setEdges,
    setNodes,
  ]);

  const updateFocus = (nodeId: string) => {
    const next = focusNodeId === nodeId ? null : nodeId;
    setFocusNodeId(next);
    setSelectedNode(
      graph.nodes.find((node) => node.node_id === nodeId) ?? null,
    );
    setSelectedEdge(null);
  };

  const resetLayout = () => {
    if (readOnly) return;
    void apiDelete(
      `/api/v1/impact-analyses/${encodeURIComponent(analysisId)}/layout`,
    ).finally(() => {
      setLayoutNonce((value) => value + 1);
      setLayoutState("idle");
    });
  };

  const coreCount = graph.edges.filter(
    (edge) => edge.order === "direct" || edge.order === "first_order",
  ).length;
  return (
    <div
      className={`impact-graph-workbench ${isFullscreen ? "is-fullscreen" : ""}`}
    >
      <div className="impact-graph-toolbar">
        <div className="impact-graph-toolbar__filters">
          <button
            type="button"
            className={`button sm ${filter.coreOnly ? "primary" : "ghost"}`}
            onClick={() =>
              onFilterChange({ ...filter, coreOnly: !filter.coreOnly })
            }
          >
            {filter.coreOnly ? `核心路径 · ${coreCount}` : "全部路径"}
          </button>
          <label className="impact-graph-confidence">
            置信度 ≥ {Math.round(filter.minimumConfidence * 100)}%
            <input
              aria-label="最低置信度"
              type="range"
              min="0"
              max="0.9"
              step="0.1"
              value={filter.minimumConfidence}
              onChange={(event) =>
                onFilterChange({
                  ...filter,
                  minimumConfidence: Number(event.target.value),
                })
              }
            />
          </label>
          <span className="impact-graph-summary">
            {filtered.nodes.length} 节点 · {filtered.edges.length} 关系
          </span>
          <button
            type="button"
            className="button ghost sm"
            onClick={() =>
              setAnnotationMode((value) =>
                value === "compact" ? "full" : "compact",
              )
            }
          >
            {annotationMode === "compact" ? "注解：简洁" : "注解：完整"}
          </button>
          {legacy ? (
            <span className="impact-graph-legacy">兼容只读图</span>
          ) : null}
        </div>
        <div className="impact-graph-toolbar__actions">
          {!readOnly ? <span className="muted">
            {layoutState === "saving"
              ? "保存布局…"
              : layoutState === "saved"
                ? "布局已保存"
                : ""}
          </span> : null}
          {!readOnly ? <button
            type="button"
            className="button ghost sm"
            onClick={resetLayout}
          >
            重置布局
          </button> : null}
          <button
            type="button"
            className="button ghost sm"
            onClick={() => setIsFullscreen((value) => !value)}
          >
            {isFullscreen ? "退出全屏" : "全屏"}
          </button>
        </div>
      </div>
      <div className="impact-graph-legend" aria-label="图例">
        <span>
          <i className="is-positive" />
          正向
        </span>
        <span>
          <i className="is-negative" />
          负向
        </span>
        <span>
          <i className="is-mixed" />
          混合
        </span>
        <span>
          <i className="is-uncertain" />
          不确定
        </span>
        <span>
          <b>实线</b>事实 · <b>虚线</b>推断/假设
        </span>
      </div>
      {!filtered.edges.length ? (
        <div className="impact-graph-empty">
          当前筛选没有可展示的因果关系。请降低置信度阈值或切换为全部路径。
        </div>
      ) : null}
      <div className="impact-graph-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          minZoom={0.25}
          maxZoom={1.8}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={(_, node) => updateFocus(node.id)}
          onEdgeClick={(_, edge) => {
            setSelectedEdge(edge.data ?? null);
            setSelectedNode(null);
          }}
          nodesDraggable={!readOnly}
          onNodeDragStop={(_, __, nextNodes) =>
            persistLayout(nextNodes as Node<GraphNodeData>[])
          }
          onMoveEnd={(_, viewport) => {
            if (readOnly) return;
            viewportRef.current = viewport;
            persistLayout(nodes);
          }}
        >
          <Background gap={24} size={1} color="#d8e1ed" />
          <Controls showInteractive={false} />
          <MiniMap
            pannable
            zoomable
            nodeColor={(node) =>
              nodeTypeColor((node.data as GraphNodeData).node_type)
            }
          />
        </ReactFlow>
      </div>
      {selectedNode || selectedEdge ? (
        <GraphDetail
          node={selectedNode}
          edge={selectedEdge}
          graph={filtered}
          onClose={() => {
            setSelectedNode(null);
            setSelectedEdge(null);
          }}
        />
      ) : null}
    </div>
  );
}

function GraphDetail({
  node,
  edge,
  graph,
  onClose,
}: {
  node: ImpactGraphNode | null;
  edge: ImpactGraphEdge | null;
  graph: ImpactGraph;
  onClose: () => void;
}) {
  const incoming = node
    ? graph.edges.filter((item) => item.target_node_id === node.node_id)
    : [];
  const outgoing = node
    ? graph.edges.filter((item) => item.source_node_id === node.node_id)
    : [];
  return (
    <aside className="impact-graph-detail">
      <div className="impact-graph-detail__header">
        <strong>{node ? "节点详情" : "因果关系"}</strong>
        <button type="button" className="button ghost sm" onClick={onClose}>
          关闭
        </button>
      </div>
      {node ? (
        <>
          <h4>{node.label}</h4>
          <p className="muted">
            {nodeLabels[node.node_type] ?? node.node_type}
            {node.group ? ` · ${node.group}` : ""}
          </p>
          <div className="impact-graph-node-paths">
            <strong>上游关系 · {incoming.length}</strong>
            {incoming.length ? (
              incoming.map((item) => (
                <span key={item.edge_id}>
                  {item.mechanism} · {edgeAnnotation(item).confidenceLabel}
                </span>
              ))
            ) : (
              <span className="muted">暂无上游关系</span>
            )}
            <strong>下游关系 · {outgoing.length}</strong>
            {outgoing.length ? (
              outgoing.map((item) => (
                <span key={item.edge_id}>
                  {item.mechanism} · {edgeAnnotation(item).confidenceLabel}
                </span>
              ))
            ) : (
              <span className="muted">暂无下游关系</span>
            )}
          </div>
        </>
      ) : null}
      {edge ? (
        <>
          <h4>{edge.mechanism}</h4>
          <dl>
            <dt>方向</dt>
            <dd>{edgeAnnotation(edge).directionLabel}</dd>
            <dt>传导阶次</dt>
            <dd>{edgeAnnotation(edge).orderLabel}</dd>
            <dt>置信度</dt>
            <dd>{edgeAnnotation(edge).confidenceLabel}</dd>
            <dt>时间</dt>
            <dd>{edgeAnnotation(edge).horizonLabel}</dd>
            <dt>推理类型</dt>
            <dd>{edgeAnnotation(edge).inferenceLabel}</dd>
          </dl>
          {edge.conditions?.length ? (
            <p>
              <strong>成立条件：</strong>
              {edge.conditions.join("；")}
            </p>
          ) : null}
          {edge.invalidators?.length ? (
            <p>
              <strong>失效条件：</strong>
              {edge.invalidators.join("；")}
            </p>
          ) : null}
          <p>
            <strong>证据：</strong>
            {edge.evidence_refs?.length
              ? edge.evidence_refs.map((ref) => (
                  <span
                    className="impact-graph-evidence"
                    key={`${ref.evidence_type}-${ref.evidence_id}`}
                  >
                    <a
                      href={`/api/v1/evidence/${encodeURIComponent(ref.evidence_id)}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {ref.evidence_type}:{ref.evidence_id}
                    </a>
                    {ref.stance ? ` · ${ref.stance}` : ""}
                  </span>
                ))
              : "暂无绑定证据"}
          </p>
        </>
      ) : null}
    </aside>
  );
}
