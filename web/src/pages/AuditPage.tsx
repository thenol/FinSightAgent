import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { apiGet } from "@/lib/api";
import { asList, formatDate } from "@/lib/format";
import type { AuditLog } from "@/types/api";

function objectLink(log: AuditLog) {
  if (!log.object_id) return "–";
  if (log.object_type === "review_task" || log.action.startsWith("review.")) {
    return <Link to={`/reviews/${log.object_id}`}>{log.object_id}</Link>;
  }
  if (log.object_type === "report" || log.action.startsWith("report.")) {
    return <Link to={`/reports/${log.object_id}`}>{log.object_id}</Link>;
  }
  if (log.object_type === "workflow" || log.action.startsWith("workflow.")) {
    return <Link to={`/workflows/${log.object_id}`}>{log.object_id}</Link>;
  }
  if (log.object_type === "source" || log.action.startsWith("source.")) {
    return <Link to="/sources">{log.object_id}</Link>;
  }
  return <span className="mono">{log.object_id}</span>;
}

export function AuditPage() {
  const [action, setAction] = useState("");
  const query = useQuery({
    queryKey: ["audit-logs"],
    queryFn: () => apiGet<AuditLog[] | { items: AuditLog[] }>("/api/v1/audit-logs"),
  });
  const logs = asList<AuditLog>(query.data);
  const actions = useMemo(
    () => [...new Set(logs.map((item) => item.action))].sort(),
    [logs],
  );
  const filtered = logs.filter((item) => !action || item.action === action);

  return (
    <>
      <PageHeader
        eyebrow="Audit"
        title="审计记录"
        description="登录、审核、发布、来源与工作流操作轨迹。"
      />
      <div className="toolbar">
        <select value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">全部操作</option>
          {actions.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </div>
      {query.isLoading ? <Skeleton /> : null}
      {query.isError ? <ErrorState>审计记录加载失败</ErrorState> : null}
      {!query.isLoading && !filtered.length ? <EmptyState>暂无审计记录</EmptyState> : null}
      <DataTable
        headers={["时间", "操作", "对象", "详情"]}
        rows={filtered.map((log) => (
          <tr key={log.id}>
            <td>{formatDate(log.created_at)}</td>
            <td>
              <StatusBadge value={log.action} />
            </td>
            <td>
              {log.object_type} · {objectLink(log)}
            </td>
            <td>
              <pre className="pre" style={{ maxHeight: "5rem" }}>
                {JSON.stringify(log.details, null, 2)}
              </pre>
            </td>
          </tr>
        ))}
      />
    </>
  );
}
