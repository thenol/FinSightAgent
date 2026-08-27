import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { OperationalMetricCard } from "@/components/OperationalMetricCard";
import { EmptyState, Skeleton } from "@/components/EmptyState";
import { apiGet } from "@/lib/api";
import { ImpactGraphFlow, type GraphFilter, type ImpactGraph } from "@/features/ImpactGraphFlow";
import { asList, formatDate, taskAge } from "@/lib/format";
import type { AdminMetrics, ResearchOverview, ReviewQueueItem, Source, Workflow } from "@/types/api";

export function OverviewPage() {
  const [graphFilter, setGraphFilter] = useState<GraphFilter>({ scenarioId: "all", horizon: "all", minimumConfidence: 0, coreOnly: true });
  const researchQuery = useQuery({
    queryKey: ["research-overview"],
    queryFn: () => apiGet<ResearchOverview>("/api/v1/overview/research?window=7d"),
  });
  const graphQuery = useQuery({
    queryKey: ["event-knowledge-graph"],
    queryFn: () => apiGet<{ nodes: ImpactGraph["nodes"]; edges: ImpactGraph["edges"]; truncated: boolean }>("/api/v1/events/graph?window=7d"),
  });
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
      apiGet<ReviewQueueItem[] | { items: ReviewQueueItem[] }>("/api/v1/review-queue/items?status_filter=pending&sort=priority_desc&limit=20"),
  });
  const workflowsQuery = useQuery({
    queryKey: ["workflows", "active"],
    queryFn: () => apiGet<Workflow[] | { items: Workflow[] }>("/api/v1/workflows?limit=200"),
  });

  const research = researchQuery.data;

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
  const reviews = asList<ReviewQueueItem>(reviewsQuery.data);
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

      <section className="research-overview panel">
        <div className="section-heading"><div><span className="eyebrow">Research cockpit</span><h2>近期事件影响</h2><p className="muted">截至 {research ? formatDate(research.as_of) : "–"}，基于已批准分析聚合</p></div><Link className="button ghost sm" to="/events">查看事件中心</Link></div>
        {researchQuery.isError ? <p className="muted">事件影响暂时不可用，运营指标仍可正常查看。</p> : research ? <>
          <div className="metric-strip compact"><div className="metric-card"><span>综合方向</span><strong><StatusBadge value={research.summary.direction} /></strong><p>{research.summary.event_count} 个重大事件</p></div><div className="metric-card"><span>正向 / 负向</span><strong>{research.summary.positive_strength.toFixed(2)} / {research.summary.negative_strength.toFixed(2)}</strong><p>影响强度</p></div><div className="metric-card"><span>平均置信度</span><strong>{Math.round(research.summary.confidence * 100)}%</strong><p>正式结论</p></div></div>
          <div className="overview-event-grid">{research.events.map((item) => <article className="overview-event-card" key={item.event.id}><div className="event-card-top"><StatusBadge value={item.direction} /><span className="muted">{formatDate(item.event.occurred_at)}</span></div><Link to={`/events/${item.event.id}`}><h3>{item.event.title}</h3></Link><p>{item.explanation || "暂无概括性影响解释"}</p><div className="tag-row">{item.affected_targets.slice(0, 4).map((target) => <Link className="tag" key={`${item.event.id}-${target.target_id}`} to={`/impact-targets/${target.target_id}`}>{target.name} · {target.direction === "positive" ? "利好" : target.direction === "negative" ? "利空" : "混合"}</Link>)}</div></article>)}</div>
        </> : <EmptyState>暂无可用的正式事件影响分析</EmptyState>}
      </section>

      <section className="panel research-graph-panel">
        <div className="section-heading"><div><span className="eyebrow">Knowledge graph</span><h2>事件关系图谱</h2><p className="muted">展示事件之间的因果、更新、放大和对冲，以及其对目标的传导。</p></div><Link className="button ghost sm" to="/events">事件中心</Link></div>
        {graphQuery.isError ? <p className="muted">事件图谱暂时不可用。</p> : graphQuery.data?.nodes.length ? <ImpactGraphFlow analysisId="overview-event-graph" graph={{ nodes: graphQuery.data.nodes, edges: graphQuery.data.edges }} scenarios={[]} legacy={false} filter={graphFilter} onFilterChange={setGraphFilter} readOnly /> : <EmptyState>近期暂无可展示的事件关系</EmptyState>}
      </section>

      <section className="metric-strip operational-metric-strip" aria-label="运行概览">
        <OperationalMetricCard label="待审任务" value={metrics?.reviews.pending ?? reviews.length} secondary={oldest ? `最早等待 ${taskAge(oldest.created_at)}` : "队列为空"} tone={reviews.length ? "attention" : "healthy"} href="/reviews?status=pending" icon="◷" />
        <OperationalMetricCard label="异常来源" value={metrics?.sources ? metrics.sources.total - (metrics.sources.by_status.active || 0) : degraded.length} secondary={`共 ${metrics?.sources.total ?? sources.length} 个来源`} tone={degraded.length ? "warning" : "healthy"} href="/sources" icon="◉" />
        <OperationalMetricCard label="工作流成功率" value={workflowSuccessRate} secondary={`${metrics?.workflows.total ?? workflows.length} 个总运行`} progress={metrics?.workflows.success_rate ?? 0} tone={(metrics?.workflows.success_rate ?? 1) < 0.9 ? "warning" : "healthy"} href="/workflows" icon="↗" />
        <OperationalMetricCard label="模型平均延迟" value={modelLatency} secondary={`24h 内 ${metrics?.models.last_24h_runs ?? 0} 次调用`} tone="neutral" href="/models" icon="◌" />
        <OperationalMetricCard label="引用完整率" value={citationRate} secondary={`${metrics?.citations.claims_with_evidence ?? 0} / ${metrics?.citations.total_claims ?? 0} Claim 含证据`} progress={metrics?.citations.completeness_rate ?? 0} tone={(metrics?.citations.completeness_rate ?? 1) < 0.8 ? "warning" : "healthy"} href="/audit" icon="✓" />
        <OperationalMetricCard label="Outbox 积压" value={metrics?.outbox.pending ?? 0} secondary={`${metrics?.outbox.dead_lettered ?? 0} 条死信`} tone={(metrics?.outbox.dead_lettered ?? 0) ? "critical" : (metrics?.outbox.pending ?? 0) ? "attention" : "healthy"} href="/workflows" icon="⌁" />
      </section>

      <div className="split">
        <section className="panel">
          <h3>待审积压</h3>
          <p className="muted">
            {reviews.length} 个待处理
            {oldest ? ` · 最老任务 ${taskAge(oldest.created_at)}（${formatDate(oldest.created_at)}）` : ""}
          </p>
          {reviews.length ? (
            <div className="overview-review-list">
              {reviews.slice(0, 5).map((task) => (
                <Link className={`overview-review-item priority-${task.priority_band}`} to={task.display.href || `/reviews/${task.id}`} key={task.id}>
                  <span className="overview-review-item__marker" aria-hidden="true" />
                  <span className="overview-review-item__body"><strong>{task.display.title}</strong><small>{task.display.type_label} · {task.display.subtitle}</small><small className="muted">{task.display.summary || reviewReasonLabel(task.reason_code)}</small></span>
                  <span className="overview-review-item__meta"><em>{priorityLabel(task.priority_band)}</em><small>{task.age_seconds >= 3600 ? `${Math.floor(task.age_seconds / 3600)}小时` : `${Math.max(1, Math.floor(task.age_seconds / 60))}分钟`}未处理</small></span>
                </Link>
              ))}
            </div>
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

function priorityLabel(value: string): string {
  return value === "critical" ? "紧急" : value === "high" ? "高优先级" : "待处理";
}

function reviewReasonLabel(value: string): string {
  const labels: Record<string, string> = {
    REPORT_REVIEW_REQUIRED: "研究报告需要审核",
    QUALITY_GATE_FAILED: "质量门禁未通过",
    LOW_CONFIDENCE: "分析置信度较低",
    CLAIM_CONFLICT: "事实证据存在冲突",
    EVENT_MERGE_CANDIDATE: "等待事件归并判断",
  };
  return labels[value] || "需要研究人员处理";
}
