import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, Skeleton } from "@/components/EmptyState";
import { apiGet } from "@/lib/api";
import { asList, formatDate, taskAge } from "@/lib/format";
import type { AdminMetrics, ReviewTask, Source, Workflow } from "@/types/api";

export function OverviewPage() {
  const metricsQuery = useQuery({
    queryKey: ["admin-metrics"],
    queryFn: () => apiGet<AdminMetrics>("/api/v1/admin/metrics"),
  });
  const sourcesQuery = useQuery({
    queryKey: ["sources"],
    queryFn: () => apiGet<Source[] | { items: Source[] }>("/api/v1/sources"),
  });
  const reviewsQuery = useQuery({
    queryKey: ["reviews", "pending"],
    queryFn: () =>
      apiGet<ReviewTask[] | { items: ReviewTask[] }>("/api/v1/reviews?status_filter=pending"),
  });
  const workflowsQuery = useQuery({
    queryKey: ["workflows", "active"],
    queryFn: () => apiGet<Workflow[] | { items: Workflow[] }>("/api/v1/workflows?limit=200"),
  });

  if (
    metricsQuery.isLoading ||
    sourcesQuery.isLoading ||
    reviewsQuery.isLoading ||
    workflowsQuery.isLoading
  ) {
    return <Skeleton />;
  }

  const metrics = metricsQuery.data;
  const sources = asList<Source>(sourcesQuery.data);
  const reviews = asList<ReviewTask>(reviewsQuery.data);
  const workflows = asList<Workflow>(workflowsQuery.data);
  const waiting = workflows.filter((item) => item.status === "waiting_review");
  const degraded = sources.filter((item) => item.status !== "active");
  const oldest = [...reviews].sort((a, b) =>
    String(a.created_at || "").localeCompare(String(b.created_at || "")),
  )[0];

  const workflowSuccessRate =
    metrics && typeof metrics.workflows.success_rate === "number"
      ? `${Math.round(metrics.workflows.success_rate * 100)}%`
      : "–";
  const citationRate =
    metrics && typeof metrics.citations.completeness_rate === "number"
      ? `${Math.round(metrics.citations.completeness_rate * 100)}%`
      : "–";
  const modelLatency =
    metrics && typeof metrics.models.avg_latency_ms === "number"
      ? `${Math.round(metrics.models.avg_latency_ms)} ms`
      : "–";

  return (
    <>
      <PageHeader
        eyebrow="Operations"
        title="运营总览"
        description="从积压、来源健康与等待恢复的工作流快速进入处理。"
        actions={
          <>
            <Link className="button primary" to="/reviews">
              进入待审
            </Link>
            <Link className="button ghost" to="/briefs">
              今日简报
            </Link>
          </>
        }
      />

      <section className="metric-strip" aria-label="运行概览">
        <article className="metric-card">
          <span>待审任务</span>
          <strong>{metrics?.reviews.pending ?? reviews.length}</strong>
          <p>{oldest ? `最老 ${taskAge(oldest.created_at)}` : "队列为空"}</p>
        </article>
        <article className="metric-card">
          <span>异常来源</span>
          <strong>{metrics?.sources ? metrics.sources.total - (metrics.sources.by_status.active || 0) : degraded.length}</strong>
          <p>共 {metrics?.sources.total ?? sources.length} 个来源</p>
        </article>
        <article className="metric-card">
          <span>工作流成功率</span>
          <strong>{workflowSuccessRate}</strong>
          <p>{metrics?.workflows.total ?? workflows.length} 个总运行</p>
        </article>
        <article className="metric-card">
          <span>模型平均延迟</span>
          <strong>{modelLatency}</strong>
          <p>24h 内 {metrics?.models.last_24h_runs ?? 0} 次调用</p>
        </article>
        <article className="metric-card">
          <span>引用完整率</span>
          <strong>{citationRate}</strong>
          <p>{metrics?.citations.claims_with_evidence ?? 0} / {metrics?.citations.total_claims ?? 0} Claim 含证据</p>
        </article>
        <article className="metric-card">
          <span>Outbox 积压</span>
          <strong>{metrics?.outbox.pending ?? 0}</strong>
          <p>{metrics?.outbox.dead_lettered ?? 0} 条死信</p>
        </article>
      </section>

      <div className="split">
        <section className="panel">
          <h3>待审积压</h3>
          <p className="muted">
            {reviews.length} 个待处理
            {oldest ? ` · 最老任务 ${taskAge(oldest.created_at)}（${formatDate(oldest.created_at)}）` : ""}
          </p>
          {reviews.length ? (
            <ul>
              {reviews.slice(0, 5).map((task) => (
                <li key={task.id}>
                  <Link to={`/reviews/${task.id}`}>
                    {task.object_type} · {task.reason_code}
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>暂无待审任务</EmptyState>
          )}
        </section>
        <section className="panel">
          <h3>异常来源</h3>
          {degraded.length ? (
            <ul>
              {degraded.slice(0, 5).map((source) => (
                <li key={source.id}>
                  <Link to="/sources">
                    {source.name} <StatusBadge value={source.status} />
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>来源全部正常</EmptyState>
          )}
        </section>
      </div>
      <section className="panel" style={{ marginTop: "0.75rem" }}>
        <h3>等待恢复的工作流</h3>
        {waiting.length ? (
          <ul>
            {waiting.slice(0, 8).map((run) => (
              <li key={run.id}>
                <Link to={`/workflows/${run.id}`}>
                  {run.id} · 节点 {run.current_node || "–"} · {run.error_code || run.status}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>没有等待审核的工作流</EmptyState>
        )}
      </section>
    </>
  );
}
