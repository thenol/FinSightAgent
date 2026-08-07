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
import { asList, formatDate, taskAge } from "@/lib/format";
import type { EventDetail, MergeReviewTask } from "@/types/api";

const DECISION_LABELS: Record<string, string> = {
  merge: "合并到候选事件",
  new_event: "创建为新事件",
  skip: "跳过",
};

export function MergeReviewsPage() {
  const { taskId } = useParams();
  if (taskId) return <MergeReviewDetailPage taskId={taskId} />;
  return <MergeReviewListPage />;
}

function MergeReviewListPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("open");

  const query = useQuery({
    queryKey: ["merge-reviews", status],
    queryFn: () =>
      apiGet<MergeReviewTask[] | { items: MergeReviewTask[] }>(
        `/api/v1/merge-reviews${status ? `?status_filter=${encodeURIComponent(status)}` : ""}`,
      ),
  });
  const tasks = asList<MergeReviewTask>(query.data);

  return (
    <>
      <PageHeader
        eyebrow="Queue"
        title="事件合并审核"
        description="人工确认文档应合并到现有事件还是创建为新事件。"
      />
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          <option value="open">待处理</option>
          <option value="decided">已决定</option>
        </select>
      </form>
      {query.isLoading ? <Skeleton /> : null}
      {query.isError ? <ErrorState>合并审核列表加载失败</ErrorState> : null}
      {!query.isLoading && !tasks.length ? <EmptyState>暂无合并审核任务</EmptyState> : null}
      <DataTable
        headers={["文档", "候选事件", "状态", "年龄", "操作"]}
        rows={tasks.map((task) => (
          <tr
            key={task.id}
            className="clickable"
            onClick={() => navigate(`/merge-reviews/${task.id}`)}
          >
            <td>
              <div className="muted mono">{task.document_id}</div>
            </td>
            <td>
              {task.candidates.map((id) => (
                <div key={id} className="muted mono">
                  {id}
                </div>
              ))}
            </td>
            <td>
              <StatusBadge value={task.status} />
              {task.decision ? <div className="muted">{DECISION_LABELS[task.decision] || task.decision}</div> : null}
            </td>
            <td>
              {taskAge(task.created_at)}
              <div className="muted">{formatDate(task.created_at)}</div>
            </td>
            <td>
              <Link
                className="button ghost"
                to={`/merge-reviews/${task.id}`}
                onClick={(e) => e.stopPropagation()}
              >
                打开
              </Link>
            </td>
          </tr>
        ))}
      />
    </>
  );
}

function MergeReviewDetailPage({ taskId }: { taskId: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const { role } = useAuth();
  const [confirm, setConfirm] = useState<(ConfirmConfig & { decision: string }) | null>(null);
  const isReviewer = role === "reviewer" || role === "admin";

  const taskQuery = useQuery({
    queryKey: ["merge-review", taskId],
    queryFn: () => apiGet<MergeReviewTask>(`/api/v1/merge-reviews/${encodeURIComponent(taskId)}`),
  });

  const candidateIds = useMemo(
    () => taskQuery.data?.candidates || [],
    [taskQuery.data?.candidates],
  );

  const eventsQuery = useQuery({
    queryKey: ["merge-review-events", candidateIds.join(",")],
    enabled: candidateIds.length > 0,
    queryFn: async () => {
      const events: EventDetail[] = [];
      for (const eventId of candidateIds) {
        events.push(await apiGet<EventDetail>(`/api/v1/events/${encodeURIComponent(eventId)}`));
      }
      return events;
    },
  });

  const decisionMutation = useMutation({
    mutationFn: (payload: { decision: string; comment: string }) =>
      apiPost(`/api/v1/merge-reviews/${encodeURIComponent(taskId)}/decision`, payload),
    onSuccess: async (_data, variables) => {
      push(`合并审核决定已提交：${DECISION_LABELS[variables.decision] || variables.decision}`);
      await queryClient.invalidateQueries({ queryKey: ["merge-reviews"] });
      await queryClient.invalidateQueries({ queryKey: ["merge-review", taskId] });
      navigate("/merge-reviews");
    },
    onError: (error) => push(error instanceof Error ? error.message : "提交失败", "error"),
  });

  if (taskQuery.isLoading) return <Skeleton />;
  if (taskQuery.isError || !taskQuery.data) return <ErrorState>合并审核详情不可用</ErrorState>;

  const task = taskQuery.data;
  const events = eventsQuery.data || [];

  return (
    <>
      <PageHeader
        eyebrow="Decision"
        title={`合并审核任务 ${task.id}`}
        description={`文档 ${task.document_id} · ${task.candidates.length} 个候选事件 · 年龄 ${taskAge(
          task.created_at,
        )}`}
        actions={
          <Link className="button ghost" to="/merge-reviews">
            返回队列
          </Link>
        }
      />
      <div className="review-layout">
        <section className="panel">
          <h3>决定</h3>
          <p className="muted mono">{task.document_id}</p>
          <div className="actions">
            {task.status === "open" && isReviewer
              ? ["merge", "new_event", "skip"].map((decision) => (
                  <button
                    key={decision}
                    type="button"
                    className={`button ${decision === "skip" ? "ghost" : "primary"}`}
                    onClick={() =>
                      setConfirm({
                        decision,
                        title: `确认${DECISION_LABELS[decision]}`,
                        message:
                          decision === "merge"
                            ? "将把当前文档关联到候选事件。"
                            : decision === "new_event"
                              ? "将基于当前文档创建一个新事件。"
                              : "将跳过本次合并建议。",
                        submitLabel: DECISION_LABELS[decision],
                        danger: decision === "skip",
                        defaultComment: `admin-ui:${decision}`,
                      })
                    }
                  >
                    {DECISION_LABELS[decision]}
                  </button>
                ))
              : <EmptyState>该任务已处理</EmptyState>}
          </div>
        </section>
        <section className="panel">
          <h3>上下文</h3>
          {eventsQuery.isLoading ? <Skeleton /> : null}
          {events.length ? (
            <div>
              <h4>候选事件</h4>
              {events.map((event) => (
                <div key={event.id} style={{ marginBottom: "0.75rem" }}>
                  <Link to={`/events/${event.id}`}>
                    {event.title}
                  </Link>
                  <div className="muted mono">{event.id}</div>
                  <p className="muted">
                    <StatusBadge value={event.event_type} /> ·{" "}
                    <StatusBadge value={event.status} /> ·{" "}
                    {formatDate(event.occurred_at)}
                  </p>
                </div>
              ))}
            </div>
          ) : null}
          {!eventsQuery.isLoading && !events.length ? (
            <EmptyState>暂无候选事件信息</EmptyState>
          ) : null}
        </section>
      </div>
      <ConfirmDialog
        open={Boolean(confirm)}
        config={confirm}
        onCancel={() => setConfirm(null)}
        onConfirm={async ({ comment }) => {
          if (!confirm) return;
          await decisionMutation.mutateAsync({
            decision: confirm.decision,
            comment,
          });
          setConfirm(null);
        }}
      />
    </>
  );
}
