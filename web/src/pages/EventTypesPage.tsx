import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { ConfirmDialog, type ConfirmConfig } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { useAuth } from "@/app/AuthContext";
import { apiGet, apiPost } from "@/lib/api";
import { asList, formatDate } from "@/lib/format";
import { canReview } from "@/lib/roles";
import type { EventTypeRegistryEntry } from "@/types/api";

export function EventTypesPage() {
  const { role } = useAuth();
  const { push } = useToast();
  const queryClient = useQueryClient();
  const reviewer = canReview(role);
  const [status, setStatus] = useState("candidate");
  const [confirm, setConfirm] = useState<(ConfirmConfig & { typeLabel: string; action: "accept" | "reject" }) | null>(
    null,
  );

  const query = useQuery({
    queryKey: ["event-types", status],
    queryFn: () =>
      apiGet<EventTypeRegistryEntry[] | { items: EventTypeRegistryEntry[] }>(
        `/api/v1/event-types${status ? `?status_filter=${encodeURIComponent(status)}` : ""}`,
      ),
  });
  const entries = asList<EventTypeRegistryEntry>(query.data);

  const decide = useMutation({
    mutationFn: async ({ typeLabel, action }: { typeLabel: string; action: "accept" | "reject" }) =>
      apiPost<EventTypeRegistryEntry>(
        `/api/v1/event-types/${encodeURIComponent(typeLabel)}/${action}`,
        {},
      ),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["event-types"] });
      push(
        variables.action === "accept"
          ? `已升格类型 ${variables.typeLabel}`
          : `已拒绝类型 ${variables.typeLabel}`,
      );
    },
    onError: (error: Error) => {
      push(error.message || "操作失败", "error");
    },
  });

  return (
    <>
      <PageHeader
        eyebrow="Taxonomy"
        title="事件类型词表"
        description="开放分类标签积累后可升格为一等类型，或拒绝后让后续同类事件落入 cold。"
      />
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          <option value="candidate">候选</option>
          <option value="accepted">已升格</option>
          <option value="rejected">已拒绝</option>
        </select>
      </form>
      {query.isLoading ? <Skeleton /> : null}
      {query.isError ? <ErrorState>事件类型列表加载失败</ErrorState> : null}
      {!query.isLoading && !entries.length ? <EmptyState>暂无候选类型</EmptyState> : null}
      <DataTable
        headers={["类型标签", "状态", "事件数", "升格就绪", "决定人", "操作"]}
        rows={entries.map((entry) => (
          <tr key={entry.type_label}>
            <td>
              <div className="mono">{entry.type_label}</div>
              <div className="muted">{formatDate(entry.updated_at || entry.created_at)}</div>
            </td>
            <td>
              <StatusBadge value={entry.status} />
            </td>
            <td>{entry.event_count}</td>
            <td>{entry.promotion_ready ? "是" : "—"}</td>
            <td>
              <span className="muted mono">{entry.decided_by || "—"}</span>
            </td>
            <td>
              {reviewer && entry.status === "candidate" ? (
                <div className="actions">
                  <button
                    type="button"
                    className="button"
                    onClick={() =>
                      setConfirm({
                        typeLabel: entry.type_label,
                        action: "accept",
                        title: "升格类型",
                        message: `将 ${entry.type_label} 升格。后续同类事件不再因候选类型强制审核。完整 Schema 仍需发版补充。`,
                        submitLabel: "升格",
                      })
                    }
                  >
                    升格
                  </button>
                  <button
                    type="button"
                    className="button ghost"
                    onClick={() =>
                      setConfirm({
                        typeLabel: entry.type_label,
                        action: "reject",
                        title: "拒绝类型",
                        message: `拒绝 ${entry.type_label}。后续同类事件将落入 cold。`,
                        submitLabel: "拒绝",
                        danger: true,
                      })
                    }
                  >
                    拒绝
                  </button>
                </div>
              ) : null}
            </td>
          </tr>
        ))}
      />
      <ConfirmDialog
        open={Boolean(confirm)}
        config={confirm}
        onCancel={() => setConfirm(null)}
        onConfirm={async () => {
          if (!confirm) return;
          await decide.mutateAsync({ typeLabel: confirm.typeLabel, action: confirm.action });
          setConfirm(null);
        }}
      />
    </>
  );
}
