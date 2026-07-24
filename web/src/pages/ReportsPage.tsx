import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { ConfirmDialog, type ConfirmConfig } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { useAuth } from "@/app/AuthContext";
import { apiGet, apiPost } from "@/lib/api";
import { asList, formatDate, transitionNames } from "@/lib/format";
import { allowedReportTransitions } from "@/lib/roles";
import type { Report } from "@/types/api";

export function ReportsPage() {
  const { reportId } = useParams();
  if (reportId) return <ReportDetailPage reportId={reportId} />;
  return <ReportListPage />;
}

function ReportListPage() {
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: ["reports"],
    queryFn: () => apiGet<Report[] | { items: Report[] }>("/api/v1/reports"),
  });
  const reports = asList<Report>(query.data);

  return (
    <>
      <PageHeader
        eyebrow="Reports"
        title="报告版本"
        description="结构化内容、溯源与角色化状态流转。"
      />
      {query.isLoading ? <Skeleton /> : null}
      {query.isError ? <ErrorState>报告列表加载失败</ErrorState> : null}
      {!query.isLoading && !reports.length ? <EmptyState>暂无报告</EmptyState> : null}
      <DataTable
        headers={["标题", "状态", "类型", "版本", "事件", "as_of"]}
        rows={reports.map((report) => (
          <tr
            key={report.id}
            className="clickable"
            onClick={() => navigate(`/reports/${report.id}`)}
          >
            <td>
              {report.title}
              <div className="muted mono">{report.id}</div>
            </td>
            <td>
              <StatusBadge value={report.status} />
            </td>
            <td>
              <StatusBadge value={report.report_type} />
            </td>
            <td>v{report.version}</td>
            <td className="mono">{report.event_id}</td>
            <td>{formatDate(report.as_of)}</td>
          </tr>
        ))}
      />
    </>
  );
}

function ReportDetailPage({ reportId }: { reportId: string }) {
  const { role } = useAuth();
  const { push } = useToast();
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState<(ConfirmConfig & { status: string }) | null>(null);
  const [diffOther, setDiffOther] = useState("");
  const [diff, setDiff] = useState<Record<string, { from: unknown; to: unknown }> | null>(null);

  const reportQuery = useQuery({
    queryKey: ["report", reportId],
    queryFn: () => apiGet<Report>(`/api/v1/reports/${encodeURIComponent(reportId)}`),
  });

  const siblingsQuery = useQuery({
    queryKey: ["event-reports", reportQuery.data?.event_id],
    enabled: Boolean(reportQuery.data?.event_id),
    queryFn: () =>
      apiGet<Report[] | { items: Report[] }>(
        `/api/v1/events/${encodeURIComponent(reportQuery.data!.event_id)}/reports`,
      ),
  });

  const transitionMutation = useMutation({
    mutationFn: (status: string) =>
      apiPost(`/api/v1/reports/${encodeURIComponent(reportId)}/transition`, { status }),
    onSuccess: async (_data, status) => {
      push(`报告已${transitionNames[status] || status}`);
      await queryClient.invalidateQueries({ queryKey: ["report", reportId] });
      await queryClient.invalidateQueries({ queryKey: ["reports"] });
    },
    onError: (error) => push(error instanceof Error ? error.message : "流转失败", "error"),
  });

  const report = reportQuery.data;
  const siblings = asList<Report>(siblingsQuery.data);
  const transitions = useMemo(
    () => (report ? allowedReportTransitions(report.status, role) : []),
    [report, role],
  );
  const content = (report?.content || {}) as Record<string, unknown>;
  const provenance = (report?.provenance || {}) as Record<string, unknown>;

  if (reportQuery.isLoading) return <Skeleton />;
  if (reportQuery.isError || !report) return <ErrorState>报告详情不可用</ErrorState>;

  return (
    <>
      <PageHeader
        eyebrow={report.report_type}
        title={report.title}
        description={`v${report.version} · ${report.id}`}
        actions={
          <Link className="button ghost" to="/reports">
            返回列表
          </Link>
        }
      />
      <div className="split">
        <section className="panel">
          <h3>结构化内容</h3>
          <StatusBadge value={report.status} />
          <p style={{ whiteSpace: "pre-wrap" }}>{report.summary}</p>
          <Field label="结论" value={content.conclusion || content.thesis} />
          <Field label="置信度" value={content.confidence} />
          <Field label="时间范围" value={content.horizon || content.time_horizon} />
          <Field label="正方观点" value={content.bull_case || content.supporting_view} />
          <Field label="反方观点" value={content.bear_case || content.counter_view} />
          <Field label="观察项" value={content.watch_items || content.observations} />
          <Field
            label="重新分析条件"
            value={content.reanalysis_triggers || content.trigger_conditions}
          />
          <p className="muted">{report.disclaimer}</p>
        </section>
        <section className="panel">
          <h3>溯源</h3>
          <Field label="workflow_id" value={provenance.workflow_id} mono />
          <Field label="model_run_ids" value={provenance.model_run_ids} mono />
          <Field label="analysis_refs" value={provenance.analysis_refs} mono />
          <Field label="tool_call_ids" value={provenance.tool_call_ids} mono />
          <p className="muted">
            as_of {formatDate(report.as_of)} · 取代 {report.supersedes_report_id || "–"}
          </p>
          <h4>状态流转</h4>
          <div className="actions">
            {transitions.length ? (
              transitions.map((status) => (
                <button
                  key={status}
                  type="button"
                  className={`button ${status === "withdrawn" ? "danger" : "primary"}`}
                  onClick={() =>
                    setConfirm({
                      status,
                      title: `确认${transitionNames[status] || status}`,
                      message: "状态流转将创建新报告版本；后端会再次校验当前状态和角色。",
                      commentRequired: false,
                      submitLabel: transitionNames[status] || status,
                      danger: status === "withdrawn",
                    })
                  }
                >
                  {transitionNames[status] || status}
                </button>
              ))
            ) : (
              <EmptyState>当前角色和状态无可用流转</EmptyState>
            )}
          </div>
        </section>
      </div>
      <section className="panel" style={{ marginTop: "0.75rem" }}>
        <h3>版本差异</h3>
        <div className="form-row">
          <select value={diffOther} onChange={(e) => setDiffOther(e.target.value)}>
            <option value="">选择对比版本</option>
            {siblings
              .filter((item) => item.id !== report.id)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  v{item.version} · {item.status} · {item.id}
                </option>
              ))}
          </select>
          <button
            type="button"
            className="button ghost"
            disabled={!diffOther}
            onClick={async () => {
              try {
                const result = await apiGet<{
                  changes: Record<string, { from: unknown; to: unknown }>;
                }>(`/api/v1/reports/${encodeURIComponent(report.id)}/diff/${encodeURIComponent(diffOther)}`);
                setDiff(result.changes || {});
              } catch (error) {
                push(error instanceof Error ? error.message : "对比失败", "error");
              }
            }}
          >
            对比
          </button>
        </div>
        {diff ? (
          Object.keys(diff).length ? (
            <DataTable
              headers={["字段", "From", "To"]}
              rows={Object.entries(diff).map(([field, change]) => (
                <tr key={field}>
                  <td>{field}</td>
                  <td>
                    <pre className="pre">{JSON.stringify(change.from, null, 2)}</pre>
                  </td>
                  <td>
                    <pre className="pre">{JSON.stringify(change.to, null, 2)}</pre>
                  </td>
                </tr>
              ))}
            />
          ) : (
            <EmptyState>两个版本无差异</EmptyState>
          )
        ) : null}
      </section>
      <ConfirmDialog
        open={Boolean(confirm)}
        config={confirm}
        onCancel={() => setConfirm(null)}
        onConfirm={async () => {
          if (!confirm) return;
          await transitionMutation.mutateAsync(confirm.status);
          setConfirm(null);
        }}
      />
    </>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: unknown;
  mono?: boolean;
}) {
  if (value === undefined || value === null || value === "") return null;
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <div style={{ marginTop: "0.65rem" }}>
      <div className="muted">{label}</div>
      <div className={mono ? "mono" : undefined} style={{ whiteSpace: "pre-wrap" }}>
        {text}
      </div>
    </div>
  );
}
