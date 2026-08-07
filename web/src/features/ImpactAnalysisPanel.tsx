import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as echarts from "echarts";
import { apiGetWithStatus, apiPost } from "@/lib/api";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import type { ImpactAnalysis as ImpactAnalysisType, ImpactTarget } from "@/types/api";

type Props = {
  eventId: string;
};

type QueryResult =
  | { kind: "analysis"; value: ImpactAnalysisType }
  | { kind: "pending" }
  | { kind: "empty" };

export function ImpactAnalysisPanel({ eventId }: Props) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["event-impact-analysis", eventId],
    queryFn: async (): Promise<QueryResult> => {
      const { data, status } = await apiGetWithStatus<ImpactAnalysisType | { status: string }>(
        `/api/v1/events/${encodeURIComponent(eventId)}/impact-analysis`
      );
      if (status === 202 && "status" in data && data.status === "pending") {
        return { kind: "pending" };
      }
      if (
        data &&
        typeof data === "object" &&
        "id" in data &&
        "event_id" in data
      ) {
        return { kind: "analysis", value: data as ImpactAnalysisType };
      }
      return { kind: "empty" };
    },
    retry: false,
    refetchInterval: (query) => {
      if (query.state.data && (query.state.data as QueryResult).kind === "pending") {
        return 3000;
      }
      return false;
    },
  });

  const generate = useMutation({
    mutationFn: () =>
      apiPost<ImpactAnalysisType>(
        `/api/v1/events/${encodeURIComponent(eventId)}/impact-analysis`,
        {}
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["event-impact-analysis", eventId] });
    },
  });

  if (query.isLoading) return <Skeleton />;

  const hasError = query.isError || generate.isError;
  const result = query.data;
  const analysis = result?.kind === "analysis" ? result.value : undefined;
  const pending = result?.kind === "pending";
  const errorMessage = generate.error
    ? `生成失败：${(generate.error as Error).message}`
    : "加载影响分析失败";

  return (
    <div className="panel">
      <div
        className="actions"
        style={{ justifyContent: "space-between", alignItems: "center" }}
      >
        <h3 style={{ margin: 0 }}>影响分析</h3>
        <button
          type="button"
          className={analysis ? "button ghost" : "button primary"}
          disabled={generate.isPending || pending}
          onClick={() => generate.mutate()}
        >
          {generate.isPending ? "生成中…" : analysis ? "重新生成" : "手动生成"}
        </button>
      </div>
      {pending ? (
        <EmptyState>系统正在自动生成影响分析，请稍候…</EmptyState>
      ) : null}
      {hasError && !analysis && !pending ? <ErrorState>{errorMessage}</ErrorState> : null}
      {!hasError && !analysis && !pending ? (
        <EmptyState>
          系统将在事实卡片发布后自动为高重要度事件生成影响分析；也可点击上方按钮手动生成。
        </EmptyState>
      ) : null}
      {analysis ? <ImpactAnalysisView analysis={analysis} /> : null}
    </div>
  );
}

function ImpactAnalysisView({ analysis }: { analysis: ImpactAnalysisType }) {
  return (
    <>
      {analysis.degraded ? (
        <p className="muted" style={{ color: "var(--warning)" }}>
          当前为规则模板生成（LLM 未启用或解析失败），结果仅供参考。
        </p>
      ) : null}
      <p className="muted">{analysis.summary}</p>
      {analysis.macro_assumptions?.length ? (
        <section style={{ marginTop: "0.75rem" }}>
          <h4>宏观假设</h4>
          <ul className="muted">
            {analysis.macro_assumptions.map((assumption: string, index: number) => (
              <li key={index}>{assumption}</li>
            ))}
          </ul>
        </section>
      ) : null}
      <section style={{ marginTop: "0.75rem" }}>
        <h4>传导路径</h4>
        <ImpactGraph analysis={analysis} />
      </section>
      <section style={{ marginTop: "0.75rem" }}>
        <h4>板块/对象影响</h4>
        <ImpactTable impacts={analysis.impacts} />
      </section>
      {analysis.watch_items?.length ? (
        <section style={{ marginTop: "0.75rem" }}>
          <h4>关注项</h4>
          <ul className="muted">
            {analysis.watch_items.map((item: string, index: number) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}

function ImpactGraph({ analysis }: { analysis: ImpactAnalysisType }) {
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current);
    const option = buildGraphOption(analysis);
    chart.setOption(option as unknown as echarts.EChartsCoreOption);
    const handleResize = () => chart.resize();
    window.addEventListener("resize", handleResize);
    return () => {
      chart.dispose();
      window.removeEventListener("resize", handleResize);
    };
  }, [analysis]);

  return <div ref={chartRef} style={{ width: "100%", height: "360px" }} />;
}

export function buildGraphOption(analysis: ImpactAnalysisType): Record<string, unknown> {
  const nodes: Array<Record<string, unknown>> = [
    {
      id: "event",
      name: analysis.event_title_snapshot || "事件",
      symbolSize: 48,
      itemStyle: { color: "#2563eb" },
      label: { show: true },
    },
  ];
  const links: Array<Record<string, unknown>> = [];
  const chainColors = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444"];

  analysis.transmission_chains.forEach((chain, chainIndex) => {
    const color = chainColors[chainIndex % chainColors.length];
    let prevNodeId = "event";

    chain.steps.forEach((step, stepIndex) => {
      const nodeId = `${chain.chain_id}-step-${stepIndex}`;
      nodes.push({
        id: nodeId,
        name: step.description,
        symbolSize: 28,
        itemStyle: { color },
        label: { show: true, fontSize: 11 },
      });
      links.push({
        source: prevNodeId,
        target: nodeId,
        label: stepIndex === 0 ? { show: true, formatter: chain.mechanism, fontSize: 10 } : { show: false },
        lineStyle: { color, curveness: 0.1 },
      });
      prevNodeId = nodeId;
    });

    const relatedImpacts = analysis.impacts.filter((impact) =>
      impact.chain_refs?.includes(chain.chain_id)
    );
    relatedImpacts.forEach((impact) => {
      const impactNodeId = `impact-${impact.target_name}`;
      nodes.push({
        id: impactNodeId,
        name: impact.target_name,
        symbolSize: 22,
        itemStyle: { color: directionColor(impact.direction) },
        label: { show: true, fontSize: 11 },
      });
      links.push({
        source: prevNodeId,
        target: impactNodeId,
        lineStyle: { color: directionColor(impact.direction), curveness: 0.1 },
      });
    });
  });

  analysis.impacts.forEach((impact) => {
    const impactNodeId = `impact-${impact.target_name}`;
    if (!nodes.some((node) => node.id === impactNodeId)) {
      nodes.push({
        id: impactNodeId,
        name: impact.target_name,
        symbolSize: 22,
        itemStyle: { color: directionColor(impact.direction) },
        label: { show: true, fontSize: 11 },
      });
      links.push({
        source: "event",
        target: impactNodeId,
        lineStyle: { color: directionColor(impact.direction), curveness: 0.1 },
      });
    }
  });

  return {
    tooltip: {
      trigger: "item",
    },
    series: [
      {
        type: "graph",
        layout: "force",
        data: nodes,
        links,
        roam: true,
        draggable: true,
        label: { position: "bottom" },
        force: {
          repulsion: 300,
          edgeLength: [60, 120],
        },
        lineStyle: { curveness: 0.1 },
      },
    ],
  };
}

export function directionColor(direction: string): string {
  switch (direction) {
    case "positive":
      return "#16a34a";
    case "negative":
      return "#dc2626";
    case "neutral":
      return "#6b7280";
    default:
      return "#d97706";
  }
}

function ImpactTable({ impacts }: { impacts: ImpactTarget[] }) {
  const sorted = [...impacts].sort(
    (a, b) =>
      magnitudeScore(b.magnitude) * b.confidence -
      magnitudeScore(a.magnitude) * a.confidence
  );

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>对象</th>
          <th>类型</th>
          <th>方向</th>
          <th>强度</th>
          <th>时域</th>
          <th>置信度</th>
          <th>依据</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((impact: ImpactTarget, index: number) => (
          <tr key={index}>
            <td>{impact.target_name}</td>
            <td>{impact.target_type}</td>
            <td>
              <span className={`badge ${impact.direction}`}>{impact.direction}</span>
            </td>
            <td>{impact.magnitude}</td>
            <td>{impact.horizon}</td>
            <td>{(impact.confidence * 100).toFixed(0)}%</td>
            <td className="muted" title={impact.rationale}>
              {impact.rationale}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function magnitudeScore(magnitude: string): number {
  const scores: Record<string, number> = {
    strong: 1.0,
    moderate: 0.6,
    weak: 0.3,
    uncertain: 0.1,
  };
  return scores[magnitude] ?? 0.1;
}
