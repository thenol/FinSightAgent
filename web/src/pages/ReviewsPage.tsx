import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { ConfirmDialog, type ConfirmConfig } from "@/components/ConfirmDialog";
import { EvidenceRail } from "@/features/EvidenceRail";
import { useToast } from "@/components/Toast";
import { apiGet, apiPost } from "@/lib/api";
import { asList, decisionNames, formatDate, slaClass, taskAge } from "@/lib/format";
import type { Conflict, EventDetail, Report, ReviewTask, Workflow } from "@/types/api";

export function ReviewsPage() {
  const { taskId } = useParams();
  if (taskId) return <ReviewDetailPage taskId={taskId} />;
  return <ReviewListPage />;
}

function ReviewListPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("pending");
  const [objectType, setObjectType] = useState("");
  const [reason, setReason] = useState("");
  const [query, setQuery] = useState("");

  const reviewsQuery = useQuery({
    queryKey: ["reviews", status],
    queryFn: () =>
      apiGet<ReviewTask[] | { items: ReviewTask[] }>(
        `/api/v1/reviews${status ? `?status_filter=${encodeURIComponent(status)}` : ""}`,
      ),
  });

  const reviews = useMemo(() => {
    const items = asList<ReviewTask>(reviewsQuery.data);
    const q = query.trim().toLowerCase();
    return items.filter(
      (task) =>
        (!objectType || task.object_type === objectType) &&
        (!reason || task.reason_code === reason) &&
        (!q ||
          [task.id, task.object_id, task.reason_code].some((value) =>
            String(value || "").toLowerCase().includes(q),
          )),
    );
  }, [reviewsQuery.data, objectType, reason, query]);

  const reasons = [...new Set(asList<ReviewTask>(reviewsQuery.data).map((t) => t.reason_code))].sort();

  return (
    <>
      <PageHeader
        eyebrow="Queue"
        title="审核队列"
        description="筛选任务并进入双栏工作台完成决定。"
      />
      <form
        className="toolbar"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
        }}
      >
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          <option value="pending">待处理</option>
          <option value="decided">已决定</option>
        </select>
        <select value={objectType} onChange={(e) => setObjectType(e.target.value)}>
          <option value="">全部对象</option>
          <option value="report">报告</option>
          <option value="workflow">工作流</option>
        </select>
        <select value={reason} onChange={(e) => setReason(e.target.value)}>
          <option value="">全部原因</option>
          {reasons.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
        <input
          placeholder="任务、对象或原因"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </form>
      {reviewsQuery.isLoading ? <Skeleton /> : null}
      {reviewsQuery.isError ? <ErrorState>审核队列加载失败</ErrorState> : null}
      {!reviewsQuery.isLoading && !reviews.length ? (
        <EmptyState>没有符合条件的审核任务</EmptyState>
      ) : (
        <DataTable
          headers={["对象", "原因", "允许决定", "任务年龄", "操作"]}
          rows={reviews.map((task) => (
            <tr
              key={task.id}
              className="clickable"
              onClick={() => navigate(`/reviews/${task.id}`)}
            >
              <td>
                <StatusBadge value={task.object_type} />
                <div className="muted mono">{task.object_id}</div>
              </td>
              <td>
                <StatusBadge value={task.reason_code} />
                <div className={`muted ${slaClass(task.created_at)}`}>SLA {taskAge(task.created_at)}</div>
              </td>
              <td>
                {(task.allowed_decisions || []).map((decision) => (
                  <StatusBadge key={decision} value={decisionNames[decision] || decision} />
                ))}
              </td>
              <td>
                {taskAge(task.created_at)}
                <div className="muted">{formatDate(task.created_at)}</div>
              </td>
              <td>
                <Link className="button ghost" to={`/reviews/${task.id}`} onClick={(e) => e.stopPropagation()}>
                  打开
                </Link>
              </td>
            </tr>
          ))}
        />
      )}
    </>
  );
}

function ReviewDetailPage({ taskId }: { taskId: string }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { push } = useToast();
  const [confirm, setConfirm] = useState<(ConfirmConfig & { decision: string }) | null>(null);
  const [activeClaim, setActiveClaim] = useState<string | null>(null);

  const taskQuery = useQuery({
    queryKey: ["review", taskId],
    queryFn: () => apiGet<ReviewTask>(`/api/v1/reviews/${encodeURIComponent(taskId)}`),
  });

  const objectQuery = useQuery({
    queryKey: ["review-object", taskQuery.data?.object_type, taskQuery.data?.object_id],
    enabled: Boolean(taskQuery.data),
    queryFn: async () => {
      const task = taskQuery.data!;
      if (task.object_type === "report") {
        return {
          kind: "report" as const,
          report: await apiGet<Report>(`/api/v1/reports/${encodeURIComponent(task.object_id)}`),
        };
      }
      if (task.object_type === "claim_conflict") {
        return {
          kind: "claim_conflict" as const,
          conflict: await apiGet<Conflict>(`/api/v1/conflicts/${encodeURIComponent(task.object_id)}`),
        };
      }
      return {
        kind: "workflow" as const,
        workflow: await apiGet<Workflow>(`/api/v1/workflows/${encodeURIComponent(task.object_id)}`),
      };
    },
  });

  const eventId =
    objectQuery.data?.kind === "report"
      ? objectQuery.data.report.event_id
      : objectQuery.data?.kind === "workflow"
        ? objectQuery.data.workflow.event_id
        : objectQuery.data?.kind === "claim_conflict"
          ? objectQuery.data.conflict.event_id
          : null;

  const eventQuery = useQuery({
    queryKey: ["event", eventId],
    enabled: Boolean(eventId),
    queryFn: () => apiGet<EventDetail>(`/api/v1/events/${encodeURIComponent(eventId!)}`),
  });

  const decisionMutation = useMutation({
    mutationFn: (payload: { decision: string; comment: string; resume_from?: string }) =>
      apiPost(`/api/v1/reviews/${encodeURIComponent(taskId)}/decision`, payload),
    onSuccess: async (_data, variables) => {
      push(`审核决定已提交：${decisionNames[variables.decision] || variables.decision}`);
      await queryClient.invalidateQueries({ queryKey: ["reviews"] });
      navigate("/reviews");
    },
    onError: (error) => push(error instanceof Error ? error.message : "提交失败", "error"),
  });

  if (taskQuery.isLoading) return <Skeleton />;
  if (taskQuery.isError || !taskQuery.data) return <ErrorState>审核详情不可用</ErrorState>;

  const task = taskQuery.data;
  const claims = eventQuery.data?.claims || [];
  const groups = ["verified", "conflicted", "unverified"] as const;

  return (
    <>
      <PageHeader
        eyebrow="Decision"
        title={`审核任务 ${task.id}`}
        description={`${task.object_type} · ${task.reason_code} · 年龄 ${taskAge(task.created_at)}`}
        actions={
          <Link className="button ghost" to="/reviews">
            返回队列
          </Link>
        }
      />
      <div className="review-layout">
        <section className="panel">
          <h3>决定</h3>
          <p className="muted mono">{task.object_id}</p>
          <p className="muted">
            resume_from：{task.resume_from || "–"} · blackboard_v：{task.blackboard_version ?? "–"}
          </p>
          <div className="actions">
            {task.status === "pending"
              ? (task.allowed_decisions || []).map((decision) => (
                  <button
                    key={decision}
                    type="button"
                    className={`button ${
                      decision === "reject" || decision === "downgrade_to_fact_card"
                        ? "danger"
                        : "primary"
                    }`}
                    onClick={() =>
                      setConfirm({
                        decision,
                        title: `确认${decisionNames[decision] || decision}`,
                        message:
                          decision === "reject" || decision === "downgrade_to_fact_card"
                            ? "该操作会改变发布路径或终止流程，请确认影响。"
                            : "提交后将按后端状态机执行，并写入审计。",
                        submitLabel: decisionNames[decision] || decision,
                        showResume: decision === "return" || decision === "return_for_supplement",
                        danger: decision === "reject" || decision === "downgrade_to_fact_card",
                        defaultComment: `admin-ui:${decision}`,
                      })
                    }
                  >
                    {decisionNames[decision] || decision}
                  </button>
                ))
              : <EmptyState>该任务已处理</EmptyState>}
          </div>
        </section>
        <section className="panel">
          <h3>上下文与证据</h3>
          {objectQuery.isLoading || eventQuery.isLoading ? <Skeleton /> : null}
          {objectQuery.data?.kind === "report" ? (
            <div>
              <h4>{objectQuery.data.report.title}</h4>
              <StatusBadge value={objectQuery.data.report.status} />
              <p style={{ whiteSpace: "pre-wrap" }}>{objectQuery.data.report.summary}</p>
            </div>
          ) : null}
          {objectQuery.data?.kind === "workflow" ? (
            <div>
              <StatusBadge value={objectQuery.data.workflow.status} />
              <p className="muted">
                节点 {objectQuery.data.workflow.current_node || "–"} ·{" "}
                {objectQuery.data.workflow.error_code || "无错误码"}
              </p>
            </div>
          ) : null}
          {objectQuery.data?.kind === "claim_conflict" ? (
            <div>
              <h4>冲突摘要</h4>
              <p style={{ whiteSpace: "pre-wrap" }}>{objectQuery.data.conflict.summary}</p>
              <p className="muted">
                类型 <StatusBadge value={objectQuery.data.conflict.conflict_type} /> · 严重度{" "}
                <StatusBadge value={objectQuery.data.conflict.severity} /> · 状态{" "}
                <StatusBadge value={objectQuery.data.conflict.status} />
              </p>
            </div>
          ) : null}
          <div className="claim-group" style={{ marginTop: "1rem" }}>
            {objectQuery.data?.kind === "claim_conflict"
              ? (() => {
                  const conflict = objectQuery.data.conflict;
                  const conflictClaims = claims.filter((claim) =>
                    conflict.claim_ids.includes(claim.id),
                  );
                  return conflictClaims.length ? (
                    <div>
                      <h4>
                        <StatusBadge value="conflicted" />（{conflictClaims.length}）
                      </h4>
                      {conflictClaims.map((claim) => (
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
                  ) : (
                    <EmptyState>未加载到相关 Claim</EmptyState>
                  );
                })()
              : groups.map((status) => {
                  const group = claims.filter((claim) => claim.status === status);
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
                        {activeClaim === claim.id ? "收起证据" : `展开证据（${claim.evidence_ids.length}）`}
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
      </div>
      <ConfirmDialog
        open={Boolean(confirm)}
        config={confirm}
        onCancel={() => setConfirm(null)}
        onConfirm={async ({ comment, resumeFrom }) => {
          if (!confirm) return;
          await decisionMutation.mutateAsync({
            decision: confirm.decision,
            comment,
            resume_from: resumeFrom || undefined,
          });
          setConfirm(null);
        }}
      />
    </>
  );
}
