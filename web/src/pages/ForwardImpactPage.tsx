import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { apiGet, apiPost } from "@/lib/api";
import type { ForwardImpactPoint, ForwardImpactWindow, ImpactPortfolioTarget } from "@/types/api";

function dateValue(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function ForwardImpactPage() {
  const { targetId = "" } = useParams();
  const now = new Date();
  const [start, setStart] = useState(dateValue(new Date(now.getTime() + 86400000)));
  const [end, setEnd] = useState(dateValue(new Date(now.getTime() + 90 * 86400000)));
  const [window, setWindow] = useState<ForwardImpactWindow | null>(null);
  const [points, setPoints] = useState<ForwardImpactPoint[]>([]);
  const targetQuery = useQuery({
    queryKey: ["impact-target", targetId],
    queryFn: () => apiGet<ImpactPortfolioTarget>(`/api/v1/impact-targets/${encodeURIComponent(targetId)}`),
    enabled: Boolean(targetId),
  });
  const target = targetQuery.data;
  const average = useMemo(() => {
    if (!points.length) return null;
    const values = points.filter((item) => item.scenario_id === "baseline");
    return values.reduce((sum, item) => sum + item.net_conditional, 0) / Math.max(values.length, 1);
  }, [points]);

  async function createWindow() {
    const created = await apiPost<ForwardImpactWindow>("/api/v1/forward-impact-windows", {
      target_id: targetId,
      as_of: now.toISOString(),
      window_start: new Date(`${start}T00:00:00Z`).toISOString(),
      window_end: new Date(`${end}T23:59:59Z`).toISOString(),
      included_kinds: ["scheduled", "conditional"],
      granularity: "auto",
      scenario_set_id: "baseline",
    });
    setWindow(created);
    const timeline = await apiGet<ForwardImpactPoint[]>(
      `/api/v1/forward-impact-windows/${encodeURIComponent(created.id)}/timeline`,
    );
    setPoints(timeline);
  }

  if (targetQuery.isLoading) return <Skeleton />;
  if (targetQuery.isError || !target) return <ErrorState>行业信息加载失败</ErrorState>;
  return (
    <>
      <PageHeader eyebrow="Forward impact" title={`${target.canonical_name} · 行业前瞻`} description="未来窗口只表达影响状态，不代表价格目标；基准与压力情景分开展示。" />
      <div className="forward-window-form">
        <label>开始日期<input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></label>
        <label>结束日期<input type="date" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
        <button className="button" type="button" onClick={() => void createWindow()}>生成前瞻窗口</button>
      </div>
      {!window ? <EmptyState>选择未来日期范围后生成行业前瞻。</EmptyState> : null}
      {window && !points.length ? <Skeleton /> : null}
      {points.length ? (
        <>
          <div className="impact-target-summary">
            <div><span className="muted">窗口状态</span><strong><StatusBadge value={window?.status || "building"} /></strong></div>
            <div><span className="muted">基准平均净影响</span><strong>{average?.toFixed(2)}</strong></div>
            <div><span className="muted">时间点</span><strong>{points.filter((item) => item.scenario_id === "baseline").length}</strong></div>
          </div>
          <div className="forward-timeline">
            {points.filter((item) => item.scenario_id === "baseline").map((point) => (
              <div className="forward-point" key={point.id}>
                <span>{new Date(point.point_at).toLocaleDateString("zh-CN")}</span>
                <strong className={`direction-${point.direction}`}>{point.net_conditional.toFixed(2)}</strong>
                <small>正 {point.positive_conditional.toFixed(2)} · 负 {point.negative_conditional.toFixed(2)}</small>
              </div>
            ))}
          </div>
        </>
      ) : null}
      <Link to={`/impact-targets/${encodeURIComponent(targetId)}`}>返回行业影响详情</Link>
    </>
  );
}
