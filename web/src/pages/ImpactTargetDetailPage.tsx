import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { DataTable } from "@/components/DataTable";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { ImpactGraphFlow, type GraphFilter, type ImpactGraph } from "@/features/ImpactGraphFlow";
import { apiGet } from "@/lib/api";
import type { ImpactDashboard, ImpactTimeline } from "@/types/api";

type GraphResponse = { legacy: boolean; causal_graph: ImpactGraph };

export function ImpactTargetDetailPage() {
  const { targetId = "" } = useParams();
  const [tab, setTab] = useState<"overview" | "graph" | "audit">("overview");
  const [publicationScope, setPublicationScope] = useState<"official" | "exploration">("official");
  const [selectedContribution, setSelectedContribution] = useState<string | null>(null);
  const [filter, setFilter] = useState<GraphFilter>({
    scenarioId: "all",
    horizon: "all",
    minimumConfidence: 0,
    coreOnly: false,
  });
  const dashboardQuery = useQuery({
    queryKey: ["impact-target-dashboard", targetId, publicationScope],
    queryFn: () => apiGet<ImpactDashboard>(`/api/v1/impact-targets/${encodeURIComponent(targetId)}/dashboard?publication_scope=${publicationScope}`),
    enabled: Boolean(targetId),
  });
  const timelineQuery = useQuery({
    queryKey: ["impact-target-timeline", targetId],
    queryFn: () => apiGet<ImpactTimeline>(`/api/v1/impact-targets/${encodeURIComponent(targetId)}/timeline?granularity=auto`),
    enabled: Boolean(targetId),
  });
  const graphQuery = useQuery({
    queryKey: ["impact-target-graph", targetId],
    queryFn: () => apiGet<GraphResponse>(`/api/v1/impact-targets/${encodeURIComponent(targetId)}/graph`),
    enabled: Boolean(targetId) && tab === "graph",
  });

  const dashboard = dashboardQuery.data;
  const snapshot = dashboard?.snapshot;
  const selected = useMemo(
    () => dashboard?.contributions.find((item) => item.contribution_id === selectedContribution),
    [dashboard?.contributions, selectedContribution],
  );
  if (dashboardQuery.isLoading) return <Skeleton />;
  if (dashboardQuery.isError) return <ErrorState>目标影响工作台加载失败</ErrorState>;
  if (!dashboard?.target) return <EmptyState>暂无已批准分析可展示</EmptyState>;

  return (
    <>
      <PageHeader
        eyebrow={`${dashboard.target.target_type} · Impact target`}
        title={dashboard.target.canonical_name}
        description={snapshot?.explanation || "暂无可用的批准影响分析"}
        actions={<Link className="button ghost" to="/impact-targets">返回目标影响</Link>}
      />
      <div className="impact-target-toolbar">
        <span className="muted">知识截止：{snapshot ? new Date(snapshot.as_of).toLocaleString("zh-CN") : "–"}</span>
        <div className="button-group" aria-label="影响范围">
          <button type="button" className={`button ghost sm ${publicationScope === "official" ? "active" : ""}`} onClick={() => setPublicationScope("official")}>正式结论</button>
          <button type="button" className={`button ghost sm ${publicationScope === "exploration" ? "active" : ""}`} onClick={() => setPublicationScope("exploration")}>探索情景</button>
        </div>
        <Link className="button ghost sm" to={`/future-events?target_id=${encodeURIComponent(dashboard.target.id)}`}>研究日历</Link>
        <Link className="button ghost sm" to={`/impact-targets/${encodeURIComponent(dashboard.target.id)}/forward`}>未来行业前瞻</Link>
      </div>
      {snapshot ? (
        <div className="impact-target-summary">
          <div><span className="muted">综合方向</span><strong><StatusBadge value={snapshot.direction} /></strong></div>
          <div><span className="muted">影响强度</span><strong>{snapshot.magnitude}</strong></div>
          <div><span className="muted">净影响</span><strong>{snapshot.net_score.toFixed(3)}</strong></div>
          <div><span className="muted">正向 / 负向</span><strong>{snapshot.positive_gross.toFixed(3)} / {snapshot.negative_gross.toFixed(3)}</strong></div>
          <div><span className="muted">置信度</span><strong>{Math.round(snapshot.confidence * 100)}%</strong></div>
        </div>
      ) : null}
      <nav className="impact-target-tabs" aria-label="目标影响视图">
        {([["overview", "总览与归因"], ["graph", "聚合传导图"], ["audit", "计算与版本"]] as const).map(([key, label]) => (
          <button key={key} type="button" className={`button sm ${tab === key ? "primary" : "ghost"}`} onClick={() => setTab(key)}>{label}</button>
        ))}
      </nav>
      {tab === "overview" ? (
        <Overview dashboard={dashboard} timeline={timelineQuery.data} selected={selected} onSelect={setSelectedContribution} />
      ) : null}
      {tab === "graph" ? (
        graphQuery.isLoading ? <Skeleton /> : graphQuery.isError || !graphQuery.data ? <ErrorState>聚合传导图加载失败</ErrorState> : (
          <ImpactGraphFlow analysisId={targetId} graph={graphQuery.data.causal_graph} scenarios={[]} legacy={graphQuery.data.legacy} filter={filter} onFilterChange={setFilter} readOnly />
        )
      ) : null}
      {tab === "audit" ? <AuditView dashboard={dashboard} /> : null}
    </>
  );
}

function Overview({ dashboard, timeline, selected, onSelect }: { dashboard: ImpactDashboard; timeline?: ImpactTimeline; selected?: ImpactDashboard["contributions"][number]; onSelect: (id: string) => void }) {
  const max = Math.max(...(dashboard.contributions.map((item) => item.effective_strength) || [0]), 0.001);
  return (
    <>
      <section className="impact-workbench-grid">
        <div className="panel">
          <h3>影响时间变化</h3>
          {!timeline?.points.length ? <EmptyState>暂无可回放的时间点</EmptyState> : (
            <div className="impact-timeline" aria-label="影响时间变化">
              {timeline.points.map((point) => {
                const width = Math.min(100, Math.abs(point.net_score) * 100 / 0.5);
                return <div className="impact-timeline-row" key={point.point_at}>
                  <time>{new Date(point.point_at).toLocaleDateString("zh-CN")}</time>
                  <div className="impact-timeline-track"><i className={point.net_score >= 0 ? "is-positive" : "is-negative"} style={{ width: `${Math.max(3, width)}%` }} /></div>
                  <strong>{point.net_score.toFixed(3)}</strong>
                </div>;
              })}
            </div>
          )}
        </div>
        <div className="panel">
          <h3>判断依据</h3>
          <p className="muted">{dashboard.calculation.formula}</p>
          <p>结果由已批准事件按重要度、分析置信度、传导路径、时间权重和事件依赖关系聚合。分析推断不等于已发生结果。</p>
          {selected ? <div className="impact-contribution-detail"><strong>{selected.event_title}</strong><p>{selected.rationale || "该事件暂无进一步文字说明。"}</p><small>有效贡献 {selected.effective_strength.toFixed(4)} · 时间权重 {selected.time_weight.toFixed(3)} · 路径置信度 {Math.round(selected.path_confidence * 100)}%</small></div> : <p className="muted">点击下方事件贡献查看详细拆解。</p>}
        </div>
      </section>
      <section className="panel">
        <h3>影响维度</h3>
        {!dashboard.dimensions?.length ? <p className="muted">暂无维度级贡献</p> : <div className="impact-dimension-grid">
          {dashboard.dimensions.map((item) => <div className="impact-dimension-card" key={item.dimension}>
            <span className="muted">{item.dimension}</span>
            <strong>{item.net_score >= 0 ? "+" : "−"}{Math.abs(item.net_score).toFixed(3)}</strong>
            <small>{item.direction} · 置信度 {Math.round(item.confidence * 100)}%</small>
          </div>)}
        </div>}
      </section>
      <section className="panel">
        <h3>事件贡献瀑布</h3>
        {!dashboard.contributions.length ? <EmptyState>暂无事件贡献</EmptyState> : <div className="impact-waterfall">
          {dashboard.contributions.map((item) => <button type="button" className="impact-waterfall-row" key={item.contribution_id} onClick={() => onSelect(item.contribution_id)}>
            <span className="impact-waterfall-label">{item.event_title}<small>{item.target_role || "直接影响"} · 关系置信度 {Math.round((item.relationship_confidence ?? 1) * 100)}%</small></span>
            <span className="impact-waterfall-track"><i className={item.direction === "negative" ? "is-negative" : "is-positive"} style={{ width: `${Math.max(4, item.effective_strength / max * 100)}%` }} /></span>
            <strong>{item.direction === "negative" ? "−" : "+"}{item.effective_strength.toFixed(3)}</strong>
            <small>{Math.round(item.contribution_share * 100)}%</small>
          </button>)}
        </div>}
      </section>
    </>
  );
}

function AuditView({ dashboard }: { dashboard: ImpactDashboard }) {
  return <section className="panel"><h3>计算与版本</h3><p className="muted">规则版本：{dashboard.calculation.rule_version}</p><DataTable headers={["事件", "分析版本", "原始强度", "重要度", "置信度", "时间权重", "依赖权重"]} rows={dashboard.contributions.map((item) => <tr key={item.contribution_id}><td>{item.event_title}</td><td className="mono">{item.analysis_id ? `${item.analysis_id} · v${item.analysis_version}` : "–"}</td><td>{item.base_strength.toFixed(4)}</td><td>{item.event_importance.toFixed(2)}</td><td>{Math.round(item.assessment_confidence * 100)}%</td><td>{item.time_weight.toFixed(3)}</td><td>{item.dependency_weight.toFixed(2)}</td></tr>)} /></section>;
}
