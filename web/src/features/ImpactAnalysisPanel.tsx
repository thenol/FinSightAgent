import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  directionColor as graphDirectionColor,
  ImpactGraphFlow,
  type GraphFilter,
  type GraphScenario,
  type ImpactGraph,
} from "./ImpactGraphFlow";
import { ApiError, apiGet, apiGetWithStatus, apiPost } from "@/lib/api";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import type {
  ImpactAnalysis as ImpactAnalysisType,
  ImpactTarget,
  PreliminaryAssessment,
} from "@/types/api";

type Props = { eventId: string };
type QueryResult =
  | { kind: "analysis"; value: ImpactAnalysisType }
  | { kind: "pending" }
  | { kind: "empty" };
type UnifiedGraphResponse = {
  schema_version: string;
  legacy: boolean;
  causal_graph: ImpactGraph;
  scenarios: GraphScenario[];
  edit_revision: number;
};

export function ImpactAnalysisPanel({ eventId }: Props) {
  const queryClient = useQueryClient();
  const preliminaryQuery = useQuery({
    queryKey: ["event-preliminary-assessment", eventId],
    queryFn: async () => {
      try {
        return await apiGet<PreliminaryAssessment>(`/api/v1/events/${encodeURIComponent(eventId)}/preliminary-assessment`);
      } catch (error) {
        if (error instanceof ApiError && error.code === "PRELIMINARY_ASSESSMENT_NOT_FOUND") return null;
        throw error;
      }
    },
    retry: false,
  });
  const query = useQuery({
    queryKey: ["event-impact-analysis", eventId],
    queryFn: async (): Promise<QueryResult> => {
      try {
        const { data, status } = await apiGetWithStatus<
          ImpactAnalysisType | { status: string }
        >(`/api/v1/events/${encodeURIComponent(eventId)}/impact-analysis`);
        if (status === 202 && "status" in data && data.status === "pending")
          return { kind: "pending" };
        if (
          data &&
          typeof data === "object" &&
          "id" in data &&
          "event_id" in data
        )
          return { kind: "analysis", value: data as ImpactAnalysisType };
        return { kind: "empty" };
      } catch (error) {
        // A missing analysis is a normal state before the fact card is
        // published; it should expose the manual generation action instead
        // of looking like a broken evidence panel.
        if (error instanceof ApiError && error.code === "IMPACT_ANALYSIS_NOT_FOUND") {
          return { kind: "empty" };
        }
        throw error;
      }
    },
    retry: false,
    refetchInterval: (value) =>
      value.state.data?.kind === "pending" ? 3000 : false,
  });
  const generate = useMutation({
    mutationFn: () =>
      apiPost<ImpactAnalysisType>(
        `/api/v1/events/${encodeURIComponent(eventId)}/impact-analysis`,
        {},
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["event-impact-analysis", eventId],
      }),
  });
  if (query.isLoading) return <Skeleton />;
  const result = query.data;
  const analysis = result?.kind === "analysis" ? result.value : undefined;
  const pending = result?.kind === "pending";
  return (
    <div className="panel">
      {preliminaryQuery.data ? <PreliminaryAssessmentCard assessment={preliminaryQuery.data} /> : null}
      <div className="panel-header">
        <h3>影响分析</h3>
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
      {query.isError || generate.isError ? (
        <ErrorState>
          {generate.error
            ? `生成失败：${(generate.error as Error).message}`
            : "加载影响分析失败"}
        </ErrorState>
      ) : null}
      {!query.isError && !generate.isError && !analysis && !pending ? (
        <EmptyState>
          系统将在事实卡片发布后自动生成影响分析；也可手动生成。
        </EmptyState>
      ) : null}
      {analysis ? <ImpactAnalysisView analysis={analysis} /> : null}
    </div>
  );
}

function PreliminaryAssessmentCard({ assessment }: { assessment: PreliminaryAssessment }) {
  const payload = assessment.assessment_payload || {};
  const scope = Array.isArray(payload.affected_scope) ? payload.affected_scope : [];
  const watchItems = Array.isArray(payload.watch_items) ? payload.watch_items : [];
  return (
    <section className={`preliminary-assessment-card status-${assessment.status}`}>
      <div className="panel-header">
        <div><h3>Agent 初步研判</h3><span className="muted">正式结论前的事件级研究假设 · v{assessment.version}</span></div>
        <div className="tag-row"><span className="tag">{assessment.direction}</span><span className="tag">置信度 {(assessment.confidence * 100).toFixed(0)}%</span><span className="tag">{assessment.status === "limited" ? "证据有限" : "可供下游参考"}</span></div>
      </div>
      <p className="preliminary-assessment-thesis">{assessment.thesis}</p>
      <p className="muted">{assessment.summary}</p>
      {scope.length ? <div className="preliminary-assessment-scope">{scope.slice(0, 6).map((item, index) => { const value = item as Record<string, unknown>; return <div key={index}><strong>{String(value.target_name || "目标")}</strong><span>{String(value.direction || "不确定")} · {String(value.horizon || "时间待定")}</span><small>{String(value.rationale || "")}</small></div>; })}</div> : null}
      {watchItems.length ? <details className="preliminary-assessment-details"><summary>关注与不确定性</summary><ul className="muted">{watchItems.slice(0, 5).map((item, index) => <li key={index}>{String(item)}</li>)}</ul></details> : null}
      <div className="preliminary-assessment-meta muted">数据截面 {new Date(assessment.as_of).toLocaleString("zh-CN")} · {assessment.generated_by}</div>
    </section>
  );
}

function ImpactAnalysisView({ analysis }: { analysis: ImpactAnalysisType }) {
  const graphQuery = useQuery({
    queryKey: ["impact-analysis-graph", analysis.id],
    queryFn: () =>
      apiGet<UnifiedGraphResponse>(
        `/api/v1/impact-analyses/${encodeURIComponent(analysis.id)}/graph`,
      ),
    retry: false,
  });
  const [filter, setFilter] = useState<GraphFilter>({
    scenarioId: "all",
    horizon: "all",
    minimumConfidence: 0.6,
    coreOnly: true,
  });
  const [versions, setVersions] = useState<ImpactAnalysisType[] | null>(null);
  const [compareVersion, setCompareVersion] = useState("");
  const [loadingVersions, setLoadingVersions] = useState(false);
  const scenarios = graphQuery.data?.scenarios?.length
    ? graphQuery.data.scenarios
    : v2Scenarios(analysis);
  const loadVersions = async () => {
    setLoadingVersions(true);
    try {
      const items = await apiGet<ImpactAnalysisType[]>(
        `/api/v1/events/${encodeURIComponent(analysis.event_id)}/impact-analysis/versions`,
      );
      setVersions(items);
      const previous = items.find((item) => item.version < analysis.version);
      if (previous) setCompareVersion(String(previous.version));
    } finally {
      setLoadingVersions(false);
    }
  };
  const previousVersion = versions?.find(
    (item) => String(item.version) === compareVersion,
  );
  return (
    <>
      {analysis.degraded ? (
        <p className="muted impact-analysis-warning">
          当前为规则模板生成（LLM 未启用或解析失败），结果仅供参考。
        </p>
      ) : null}
      {analysis.quality_report?.gate_passed === false ? (
        <p className="muted impact-analysis-warning">
          质量门禁未通过：当前结果仅作为待审核草稿。
          {analysis.quality_report.blockers?.length
            ? ` 阻断项 ${analysis.quality_report.blockers.length} 个。`
            : ""}
        </p>
      ) : null}
      <p className="muted">{analysis.summary}</p>
      <div className="actions impact-analysis-actions">
        <button
          type="button"
          className="button ghost sm"
          onClick={loadVersions}
          disabled={loadingVersions}
        >
          {loadingVersions ? "读取版本中…" : "版本对比"}
        </button>
        {previousVersion ? (
          <span className="muted">对比 v{previousVersion.version}</span>
        ) : null}
      </div>
      {previousVersion ? (
        <VersionDiff current={analysis} previous={previousVersion} />
      ) : null}
      {scenarios.length ? (
        <div className="impact-analysis-filters">
          <label>
            情景
            <select
              value={filter.scenarioId}
              onChange={(event) =>
                setFilter({ ...filter, scenarioId: event.target.value })
              }
            >
              <option value="all">全部情景</option>
              {scenarios.map((scenario) => (
                <option key={scenario.scenario_id} value={scenario.scenario_id}>
                  {scenario.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            时间
            <select
              value={filter.horizon}
              onChange={(event) =>
                setFilter({ ...filter, horizon: event.target.value })
              }
            >
              <option value="all">全部时间</option>
              <option value="0_1d">0–1日</option>
              <option value="2_5d">2–5日</option>
              <option value="1_4w">1–4周</option>
              <option value="1_4q">1–4季度</option>
              <option value="1y_plus">1年以上</option>
            </select>
          </label>
          {analysis.quality_report?.evidence_coverage !== undefined ? (
            <span className="muted">
              证据覆盖率{" "}
              {(analysis.quality_report.evidence_coverage * 100).toFixed(0)}%
            </span>
          ) : null}
        </div>
      ) : null}
      {analysis.macro_assumptions?.length ? (
        <section className="impact-analysis-notes">
          <h4>宏观假设</h4>
          <ul className="muted">
            {analysis.macro_assumptions.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}
      <section className="impact-analysis-graph">
        <div className="panel-header">
          <h4>传导路径</h4>
          <span className="muted">点击节点聚焦上下游，点击关系查看依据</span>
        </div>
        {graphQuery.isLoading ? <Skeleton /> : null}
        {graphQuery.isError ? <ErrorState>因果图加载失败</ErrorState> : null}
        {graphQuery.data?.causal_graph ? (
          <ImpactGraphFlow
            analysisId={analysis.id}
            graph={graphQuery.data.causal_graph}
            scenarios={scenarios}
            legacy={graphQuery.data.legacy}
            filter={filter}
            onFilterChange={setFilter}
          />
        ) : null}
      </section>
      <section className="impact-analysis-notes">
        <h4>板块/对象影响</h4>
        <ImpactTable impacts={analysis.impacts} />
      </section>
      {analysis.watch_items?.length ? (
        <section className="impact-analysis-notes">
          <h4>关注项</h4>
          <ul className="muted">
            {analysis.watch_items.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}

function v2Scenarios(analysis: ImpactAnalysisType): GraphScenario[] {
  const scenarios = analysis.analysis_payload?.scenarios;
  return Array.isArray(scenarios)
    ? scenarios.filter(
        (item): item is GraphScenario =>
          typeof item === "object" &&
          item !== null &&
          typeof (item as { scenario_id?: unknown }).scenario_id === "string" &&
          typeof (item as { name?: unknown }).name === "string" &&
          Array.isArray(
            (item as { active_edge_ids?: unknown }).active_edge_ids,
          ),
      )
    : [];
}

function VersionDiff({
  current,
  previous,
}: {
  current: ImpactAnalysisType;
  previous: ImpactAnalysisType;
}) {
  const currentTargets = new Set(
    current.impacts.map((impact) => impact.target_name),
  );
  const previousTargets = new Set(
    previous.impacts.map((impact) => impact.target_name),
  );
  const added = [...currentTargets].filter(
    (target) => !previousTargets.has(target),
  );
  const removed = [...previousTargets].filter(
    (target) => !currentTargets.has(target),
  );
  const changed = current.impacts.filter((impact) => {
    const old = previous.impacts.find(
      (item) => item.target_name === impact.target_name,
    );
    return (
      old &&
      (old.direction !== impact.direction || old.magnitude !== impact.magnitude)
    );
  });
  return (
    <section className="impact-version-diff">
      <strong>
        版本变化 v{previous.version} → v{current.version}
      </strong>
      <span>新增：{added.length ? added.join("、") : "无"}</span>
      <span>移除：{removed.length ? removed.join("、") : "无"}</span>
      <span>
        方向/强度变化：
        {changed.length
          ? changed.map((item) => item.target_name).join("、")
          : "无"}
      </span>
    </section>
  );
}

export const directionColor = graphDirectionColor;

function ImpactTable({ impacts }: { impacts: ImpactTarget[] }) {
  const sorted = [...impacts].sort(
    (a, b) =>
      magnitudeScore(b.magnitude) * b.confidence -
      magnitudeScore(a.magnitude) * a.confidence,
  );
  return (
    <div className="table-panel">
      <table className="data-table">
        <thead>
          <tr>
            <th>对象</th>
            <th>类型</th>
            <th>方向</th>
            <th>强度</th>
            <th>时域</th>
            <th>置信度</th>
            <th>依据与解释</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((impact, index) => (
            <tr key={index}>
              <td>{impact.target_name}</td>
              <td>{impact.target_type}</td>
              <td>
                <span className={`badge ${impact.direction}`}>
                  {impact.direction}
                </span>
              </td>
              <td>{impact.magnitude}</td>
              <td>{impact.horizon}</td>
              <td>{(impact.confidence * 100).toFixed(0)}%</td>
              <td className="impact-rationale" title={impact.rationale}>
                <div className="muted">{impact.rationale}</div>
                <div className="impact-rationale__plain">
                  <strong>通俗解释：</strong>
                  {explainImpactForNonEconomist(impact)}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const impactDirectionLabels: Record<string, string> = {
  positive: "支持/有利",
  negative: "压制/不利",
  neutral: "影响有限或中性",
  mixed: "同时存在有利和不利影响",
  uncertain: "方向暂不确定",
};

const impactMagnitudeLabels: Record<string, string> = {
  strong: "明显影响",
  moderate: "中等影响",
  weak: "较小影响",
  uncertain: "影响程度暂不确定",
};

const impactHorizonLabels: Record<string, string> = {
  short: "较快出现（短期）",
  medium: "需要一段时间传导（中期）",
  long: "较长时间后观察（长期）",
  uncertain: "出现时间暂不确定",
};

const impactTypeLabels: Record<string, string> = {
  sector: "行业板块",
  industry: "行业",
  company: "公司",
  macro_variable: "宏观指标",
  market: "整体市场",
  asset_class: "资产类别",
};

export function explainImpactForNonEconomist(impact: ImpactTarget): string {
  const targetType = impactTypeLabels[impact.target_type] ?? "这个对象";
  const direction = impactDirectionLabels[impact.direction] ?? "方向暂不确定";
  const magnitude =
    impactMagnitudeLabels[impact.magnitude] ?? "影响程度暂不确定";
  const horizon = impactHorizonLabels[impact.horizon] ?? "出现时间暂不确定";
  return `${targetType}“${impact.target_name}”预计会受到${direction}的影响，属于${magnitude}，${horizon}。这是一种基于当前信息和传导逻辑的分析推断，不代表结果已经发生。`;
}

function magnitudeScore(magnitude: string): number {
  return (
    (
      { strong: 1, moderate: 0.6, weak: 0.3, uncertain: 0.1 } as Record<
        string,
        number
      >
    )[magnitude] ?? 0.1
  );
}
