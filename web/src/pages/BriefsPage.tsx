import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { apiGet } from "@/lib/api";
import type { Brief } from "@/types/api";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export function BriefsPage() {
  const [date, setDate] = useState(today());
  const [selected, setSelected] = useState(today());

  const query = useQuery({
    queryKey: ["brief", selected],
    queryFn: () => apiGet<Brief>(`/api/v1/briefs/daily?date=${encodeURIComponent(selected)}`),
  });

  return (
    <>
      <PageHeader eyebrow="Brief" title="每日 Top-N 简报" description="稳定重放的候选排序结果。" />
      <form
        className="toolbar"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          setSelected(date);
        }}
      >
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
        <button className="button ghost" type="submit">
          加载
        </button>
      </form>
      {query.isLoading ? <Skeleton /> : null}
      {query.isError ? <ErrorState>简报加载失败</ErrorState> : null}
      {query.data ? (
        <>
          <p className="muted">
            {query.data.brief_date} · 候选 {query.data.candidate_count} · 规则{" "}
            {query.data.rule_version}
          </p>
          {!query.data.entries.length ? <EmptyState>该日无简报条目</EmptyState> : null}
          <DataTable
            headers={["排名", "标题", "紧迫度", "分数构成", "总分"]}
            rows={query.data.entries.map((entry) => (
              <tr key={`${entry.rank}-${entry.report_id}`}>
                <td>{entry.rank}</td>
                <td>
                  <Link to={`/reports/${entry.report_id}`}>{entry.title}</Link>
                  <div className="muted mono">{(entry.entity_ids || []).join(", ") || "–"}</div>
                </td>
                <td>
                  <StatusBadge value={entry.urgency} />
                </td>
                <td className="mono">
                  I {entry.importance.toFixed(2)} · U {entry.urgency} · C{" "}
                  {entry.confidence.toFixed(2)} · N {entry.novelty.toFixed(2)} · R{" "}
                  {entry.recency.toFixed(2)}
                </td>
                <td>{entry.score.toFixed(3)}</td>
              </tr>
            ))}
          />
        </>
      ) : null}
    </>
  );
}
