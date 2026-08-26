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
import type { Conflict, EventDetail, Report, ReviewQueueItem, ReviewQueueOverview, ReviewTask, Workflow } from "@/types/api";

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
  const [risk, setRisk] = useState("");
  const [sort, setSort] = useState("priority_desc");
  const [query, setQuery] = useState("");

  const overviewQuery = useQuery({
    queryKey: ["review-queue-overview"],
    queryFn: () => apiGet<ReviewQueueOverview>("/api/v1/review-queue/overview"),
    refetchInterval: 10000,
  });
  const reviewsQuery = useQuery({
    queryKey: ["review-queue-items", status, objectType, risk, sort],
    queryFn: () => apiGet<ReviewQueueItem[]>(`/api/v1/review-queue/items?limit=500${status ? `&status_filter=${encodeURIComponent(status)}` : ""}${objectType ? `&object_type=${encodeURIComponent(objectType)}` : ""}${risk ? `&risk_level=${encodeURIComponent(risk)}` : ""}&sort=${sort}`),
    refetchInterval: 10000,
  });

  const reviews = useMemo(() => {
    const items = asList<ReviewQueueItem>(reviewsQuery.data);
    const q = query.trim().toLowerCase();
    return items.filter(
      (task) =>
        (!objectType || task.object_type === objectType) &&
        (!status || task.review_state === status) &&
        (!risk || task.risk_level === risk) &&
        (!reason || task.reason_code === reason) &&
        (!q ||
          [task.id, task.object_id, task.reason_code].some((value) =>
            String(value || "").toLowerCase().includes(q),
          )),
    );
  }, [reviewsQuery.data, objectType, reason, query, status, risk]);

  const reasons = [...new Set(asList<ReviewQueueItem>(reviewsQuery.data).map((t) => t.reason_code))].sort();
  const counts = overviewQuery.data?.counts || {};
  const stateNames: Record<string, string> = { pending: "待 Agent", agent_processing: "Agent 处理中", agent_decided: "Agent 已决定", escalated_to_human: "等待人工", decided: "已完成", sla_breached: "SLA 超时" };
  const total = overviewQuery.data?.total || 0;
  const slaRate = total ? Math.round(((counts.sla_breached || 0) / total) * 100) : 0;

  return (
    <>
      <PageHeader
        eyebrow="Queue"
        title="审核队列"
        description="筛选任务并进入双栏工作台完成决定。"
      />
      <ReviewQueueOverview counts={counts} total={total} oldestPendingAt={overviewQuery.data?.oldest_pending_at} slaRate={slaRate} activeStatus={status} stateNames={stateNames} isRefreshing={overviewQuery.isFetching || reviewsQuery.isFetching} refreshedAt={overviewQuery.data?.refreshed_at} onStatusChange={setStatus} onRefresh={() => { void overviewQuery.refetch(); void reviewsQuery.refetch(); }} />
      <form
        className="toolbar"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
        }}
      >
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="审核状态">
          <option value="">全部状态</option>
          <option value="pending">待 Agent</option>
          <option value="escalated_to_human">等待人工</option>
          <option value="agent_decided">Agent 已决定</option>
          <option value="decided">已完成</option>
          <option value="sla_breached">SLA 超时</option>
        </select>
        <select value={objectType} onChange={(e) => setObjectType(e.target.value)} aria-label="审核对象类型">
          <option value="">全部对象</option>
          <option value="report">报告</option>
          <option value="workflow">工作流</option>
          <option value="claim_conflict">事实冲突</option>
          <option value="merge_review">事件合并</option>
        </select>
        <select value={risk} onChange={(e) => setRisk(e.target.value)} aria-label="风险等级">
          <option value="">全部风险</option>
          <option value="high">高风险</option>
          <option value="normal">普通</option>
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="排序方式">
          <option value="priority_desc">优先级最高</option>
          <option value="sla_asc">等待最久</option>
          <option value="created_desc">最新创建</option>
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
          headers={["优先级", "审核对象", "关联事件", "队列状态", "Agent 结果", "SLA / 操作"]}
          rows={reviews.map((task) => (
            <tr
              key={task.id}
              className={`clickable review-row-status-${task.review_state}`}
              onClick={() => navigate(task.display?.href || `/reviews/${task.id}`)}
            >
              <td><StatusBadge value={task.priority_band} /><strong className="review-priority-score">{task.priority_score}</strong><div className="muted">{task.priority_reasons[0] || "常规审核"}</div></td>
              <td className="review-object-cell"><div className="review-object-title">{task.display?.title || task.object_id}</div><div className="muted">{task.display?.subtitle || task.object_type}</div><div className="muted review-object-summary">{task.display?.summary}</div><button className="copy-id" type="button" onClick={(e) => { e.stopPropagation(); void navigator.clipboard?.writeText(task.object_id); }}>复制 ID</button></td>
              <td>{task.context?.event_title ? <><div>{task.context.event_title}</div><div className="muted">{task.context.event_type || "事件"} · {task.context.occurred_at ? formatDate(task.context.occurred_at) : "–"}</div></> : <span className="muted">无关联事件</span>}</td>
              <td><StatusBadge value={task.review_state} /><div className="muted"><StatusBadge value={task.risk_level} /> · {task.reason_code}</div></td>
              <td>{task.last_auto_review_status ? <><StatusBadge value={task.last_auto_review_status} /><div className="muted">{task.last_auto_review_confidence == null ? "–" : `${Math.round(task.last_auto_review_confidence * 100)}%`} · {task.last_auto_review_reason || "无说明"}</div></> : "尚未尝试"}</td>
              <td>
                <div className={slaClass(task.created_at)}>等待 {taskAge(task.created_at)}</div><div className="muted">{formatDate(task.created_at)}</div>
                <Link className="button ghost" to={task.display?.href || `/reviews/${task.id}`} onClick={(e) => e.stopPropagation()}>
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

function ReviewQueueOverview({
  counts,
  total,
  oldestPendingAt,
  slaRate,
  activeStatus,
  stateNames,
  isRefreshing,
  refreshedAt,
  onStatusChange,
  onRefresh,
}: {
  counts: Record<string, number>;
  total: number;
  oldestPendingAt?: string | null;
  slaRate: number;
  activeStatus: string;
  stateNames: Record<string, string>;
  isRefreshing: boolean;
  refreshedAt?: string;
  onStatusChange: (status: string) => void;
  onRefresh: () => void;
}) {
  const statuses = [
    "pending",
    "agent_processing",
    "escalated_to_human",
    "agent_decided",
    "decided",
    "sla_breached",
  ];
  const humanPending = counts.escalated_to_human || 0;
  return (
    <section className="review-queue-overview" aria-label="审核队列概览">
      <div className="review-queue-overview__head">
        <div>
          <span className="eyebrow">Queue overview</span>
          <h3>队列概览</h3>
          <p className="muted">按状态、风险和 SLA 快速定位需要优先处理的任务。</p>
        </div>
        <div className="review-queue-overview__refresh">
          <span className="muted">{isRefreshing ? "正在刷新…" : refreshedAt ? `更新于 ${formatDate(refreshedAt)}` : "等待更新"}</span>
          <button className="button ghost" type="button" onClick={onRefresh} disabled={isRefreshing}>{isRefreshing ? "刷新中" : "刷新队列"}</button>
        </div>
      </div>
      <div className="review-queue-overview__headline">
        <div className="review-queue-total"><span className="muted">总任务</span><strong>{total}</strong><span className="muted">当前队列</span></div>
        <div className="review-queue-kpis">
          <div><span className="muted">等待人工</span><strong className="is-warn">{humanPending}</strong><small>需人工介入</small></div>
          <div><span className="muted">SLA 超时</span><strong className="is-bad">{counts.sla_breached || 0}</strong><small>{slaRate}% 的队列</small></div>
          <div><span className="muted">已完成</span><strong className="is-ok">{counts.decided || 0}</strong><small>已处理任务</small></div>
        </div>
      </div>
      <div className="review-queue-overview__distribution">
        <div className="review-queue-overview__section-label"><span>状态分布</span><button type="button" className={!activeStatus ? "is-active" : ""} onClick={() => onStatusChange("")}>查看全部</button></div>
        <div className="review-queue-status-list">
          {statuses.map((key) => {
            const count = counts[key] || 0;
            const ratio = total ? Math.round((count / total) * 100) : 0;
            return <button type="button" key={key} className={`review-queue-status-item status-${key} ${activeStatus === key ? "is-active" : ""}`} onClick={() => onStatusChange(key)}><span className="review-queue-status-item__label"><i />{stateNames[key] || key}</span><strong>{count}</strong><span className="muted">{ratio}%</span><span className="review-queue-status-item__bar"><i style={{ width: `${count ? Math.max(4, ratio) : 0}%` }} /></span></button>;
          })}
        </div>
      </div>
      <div className="review-queue-overview__foot"><span>最早待处理 <strong>{oldestPendingAt ? taskAge(oldestPendingAt) : "暂无"}</strong></span><span>人工待办 <strong>{humanPending} 条</strong></span><span>SLA 超时率 <strong>{slaRate}%</strong></span></div>
    </section>
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

  const timelineQuery = useQuery({
    queryKey: ["review-timeline", taskId],
    queryFn: () => apiGet<Array<{ type: string; at: string; details: Record<string, unknown> }>>(`/api/v1/review-queue/${encodeURIComponent(taskId)}/timeline`),
    enabled: Boolean(taskId),
    refetchInterval: 10000,
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
          <h3>审核轨迹</h3>
          {timelineQuery.isLoading ? <Skeleton /> : null}
          <div className="claim-group">
            {(timelineQuery.data || []).map((item, index) => (
              <article className="claim-card" key={`${item.type}-${item.at}-${index}`}>
                <StatusBadge value={item.type} /> <span className="muted">{formatDate(item.at)}</span>
                <div className="muted" style={{ marginTop: "0.35rem" }}>{JSON.stringify(item.details)}</div>
              </article>
            ))}
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
