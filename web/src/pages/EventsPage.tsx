import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { EvidenceRail } from "@/features/EvidenceRail";
import { apiGet } from "@/lib/api";
import { asList, formatDate, matchesEventScope, type EventListScope } from "@/lib/format";
import type { EventDetail, EventItem } from "@/types/api";

export function EventsPage() {
  const { eventId } = useParams();
  if (eventId) return <EventDetailPage eventId={eventId} />;
  return <EventListPage />;
}

function EventListPage() {
  const navigate = useNavigate();
  const [scope, setScope] = useState<EventListScope>("research");
  const query = useQuery({
    queryKey: ["events"],
    queryFn: () => apiGet<EventItem[] | { items: EventItem[] }>("/api/v1/events"),
  });
  const events = asList<EventItem>(query.data).filter((event) =>
    matchesEventScope(event.event_type, scope),
  );

  return (
    <>
      <PageHeader
        eyebrow="Events"
        title="事件列表"
        description="默认只看五类研究事件；综合资讯与范围外可切换查看。"
      />
      <div className="actions" style={{ marginBottom: "0.75rem" }}>
        {(
          [
            ["research", "五类研究"],
            ["general", "综合资讯"],
            ["all", "全部"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={scope === value ? "button primary" : "button ghost"}
            onClick={() => setScope(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {query.isLoading ? <Skeleton /> : null}
      {query.isError ? <ErrorState>事件列表加载失败</ErrorState> : null}
      {!query.isLoading && !events.length ? <EmptyState>暂无符合筛选的事件</EmptyState> : null}
      <DataTable
        headers={["类型", "标题", "实体", "置信度", "重要度", "发生时间"]}
        rows={events.map((event) => (
          <tr key={event.id} className="clickable" onClick={() => navigate(`/events/${event.id}`)}>
            <td>
              <StatusBadge value={event.event_type} />
            </td>
            <td>{event.title}</td>
            <td className="mono">{(event.entity_ids || []).join(", ") || "–"}</td>
            <td>{Number(event.confidence ?? 0).toFixed(2)}</td>
            <td>{Number(event.importance ?? 0).toFixed(2)}</td>
            <td>{formatDate(event.occurred_at)}</td>
          </tr>
        ))}
      />
    </>
  );
}

function EventDetailPage({ eventId }: { eventId: string }) {
  const [activeClaim, setActiveClaim] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["event", eventId],
    queryFn: () => apiGet<EventDetail>(`/api/v1/events/${encodeURIComponent(eventId)}`),
  });

  if (query.isLoading) return <Skeleton />;
  if (query.isError || !query.data) return <ErrorState>事件详情不可用</ErrorState>;

  const event = query.data;
  const keyFields = Object.entries(event.key_fields || {});
  const groups = ["verified", "conflicted", "unverified"] as const;

  return (
    <>
      <PageHeader
        eyebrow={event.event_type}
        title={event.title}
        description={`重要度 ${Number(event.importance || 0).toFixed(2)} · 置信度 ${Number(event.confidence || 0).toFixed(2)} · 版本 ${event.version}`}
        actions={
          <Link className="button ghost" to="/events">
            返回列表
          </Link>
        }
      />
      <section className="panel">
        <h3>关键字段</h3>
        {event.missing_required?.length ? (
          <p className="muted">缺失：{event.missing_required.join(", ")}</p>
        ) : (
          <p className="muted">无缺失必填字段</p>
        )}
        {keyFields.length ? (
          <div className="key-grid">
            {keyFields.map(([key, value]) => (
              <div key={key} className="key-item">
                <span>{key}</span>
                <strong className="mono">{typeof value === "string" ? value : JSON.stringify(value)}</strong>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState>暂无关键字段</EmptyState>
        )}
      </section>
      <section className="panel" style={{ marginTop: "0.75rem" }}>
        <h3>Claims</h3>
        <div className="claim-group">
          {groups.map((status) => {
            const group = (event.claims || []).filter((claim) => claim.status === status);
            return (
              <div key={status}>
                <h4>
                  <StatusBadge value={status} />（{group.length}）
                </h4>
                {group.map((claim) => (
                  <article key={claim.id} className="claim-card">
                    <strong>{claim.subject_text}</strong> · {claim.predicate}
                    <div className="muted mono">{JSON.stringify(claim.object_value)}</div>
                    <button
                      type="button"
                      className="button ghost"
                      onClick={() =>
                        setActiveClaim((current) => (current === claim.id ? null : claim.id))
                      }
                    >
                      {activeClaim === claim.id
                        ? "收起证据"
                        : `展开证据（${claim.evidence_ids.length}）`}
                    </button>
                    {activeClaim === claim.id ? (
                      <EvidenceRail evidenceIds={claim.evidence_ids || []} />
                    ) : null}
                  </article>
                ))}
              </div>
            );
          })}
        </div>
      </section>
      <section className="panel" style={{ marginTop: "0.75rem" }}>
        <h3>关联报告</h3>
        {event.fact_card_id ? (
          <Link className="button ghost" to={`/reports/${event.fact_card_id}`}>
            查看 {event.fact_card_id}
          </Link>
        ) : (
          <EmptyState>无已生成报告</EmptyState>
        )}
      </section>
    </>
  );
}
