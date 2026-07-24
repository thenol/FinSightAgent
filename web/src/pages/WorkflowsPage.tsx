import { useState } from "react";
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
import { asList, formatDate } from "@/lib/format";
import { canReview, canRunWorkflow } from "@/lib/roles";
import type { BudgetEntry, NodeAttempt, Workflow } from "@/types/api";

const NODES = ["context", "fact_check", "company", "skeptic", "synthesize", "draft", "guardrail"];

export function WorkflowsPage() {
  const { workflowId } = useParams();
  if (workflowId) return <WorkflowDetailPage workflowId={workflowId} />;
  return <WorkflowListPage />;
}

function WorkflowListPage() {
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: ["workflows"],
    queryFn: () => apiGet<Workflow[] | { items: Workflow[] }>("/api/v1/workflows?limit=200"),
  });
  const workflows = asList<Workflow>(query.data);

  return (
    <>
      <PageHeader eyebrow="Runtime" title="研究工作流" description="节点时间线、预算与恢复。" />
      {query.isLoading ? <Skeleton /> : null}
      {query.isError ? <ErrorState>工作流列表加载失败</ErrorState> : null}
      {!query.isLoading && !workflows.length ? <EmptyState>暂无工作流运行</EmptyState> : null}
      <DataTable
        headers={["Workflow", "状态", "当前节点", "版本", "事件", "错误"]}
        rows={workflows.map((workflow) => (
          <tr
            key={workflow.id}
            className="clickable"
            onClick={() => navigate(`/workflows/${workflow.id}`)}
          >
            <td className="mono">{workflow.id}</td>
            <td>
              <StatusBadge value={workflow.status} />
            </td>
            <td>{workflow.current_node || "–"}</td>
            <td>v{workflow.state_version}</td>
            <td className="mono">{workflow.event_id}</td>
            <td>{workflow.error_code || "–"}</td>
          </tr>
        ))}
      />
    </>
  );
}

function WorkflowDetailPage({ workflowId }: { workflowId: string }) {
  const { role } = useAuth();
  const { push } = useToast();
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState<(ConfirmConfig & { mode: "resume" | "fact" }) | null>(
    null,
  );
  const [showBoard, setShowBoard] = useState(false);

  const workflowQuery = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => apiGet<Workflow>(`/api/v1/workflows/${encodeURIComponent(workflowId)}`),
  });
  const budgetQuery = useQuery({
    queryKey: ["workflow-budget", workflowId],
    queryFn: () =>
      apiGet<BudgetEntry[] | { items: BudgetEntry[] }>(
        `/api/v1/workflows/${encodeURIComponent(workflowId)}/budget`,
      ),
  });
  const attemptsQuery = useQuery({
    queryKey: ["workflow-attempts", workflowId],
    queryFn: () =>
      apiGet<NodeAttempt[] | { items: NodeAttempt[] }>(
        `/api/v1/workflows/${encodeURIComponent(workflowId)}/attempts`,
      ),
  });

  const resumeMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiPost(`/api/v1/workflows/${encodeURIComponent(workflowId)}/resume`, payload),
    onSuccess: async () => {
      push("工作流操作已提交");
      await queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] });
      await queryClient.invalidateQueries({ queryKey: ["workflow-budget", workflowId] });
      await queryClient.invalidateQueries({ queryKey: ["workflow-attempts", workflowId] });
    },
    onError: (error) => push(error instanceof Error ? error.message : "恢复失败", "error"),
  });
  const runMutation = useMutation({
    mutationFn: () => apiPost(`/api/v1/workflows/${encodeURIComponent(workflowId)}/run`, {}),
    onSuccess: async () => {
      push("工作流已启动");
      await queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] });
      await queryClient.invalidateQueries({ queryKey: ["workflow-budget", workflowId] });
      await queryClient.invalidateQueries({ queryKey: ["workflow-attempts", workflowId] });
    },
    onError: (error) => push(error instanceof Error ? error.message : "启动失败", "error"),
  });

  if (workflowQuery.isLoading) return <Skeleton />;
  if (workflowQuery.isError || !workflowQuery.data) return <ErrorState>工作流详情不可用</ErrorState>;

  const workflow = workflowQuery.data;
  const budget = asList<BudgetEntry>(budgetQuery.data);
  const attempts = asList<NodeAttempt>(attemptsQuery.data);
  const canStart = canRunWorkflow(role) && workflow.status === "pending";
  const canResume =
    canReview(role) && ["waiting_review", "failed"].includes(workflow.status);
  const settled = Object.fromEntries(
    budget
      .filter((entry) => entry.entry_type === "settle")
      .reduce<Map<string, number>>((map, entry) => {
        map.set(entry.dimension, (map.get(entry.dimension) || 0) + entry.amount);
        return map;
      }, new Map()),
  );

  return (
    <>
      <PageHeader
        eyebrow={workflow.budget_profile || "workflow"}
        title={`工作流 ${workflow.id}`}
        description={`节点 ${workflow.current_node || "–"} · v${workflow.state_version} · as_of ${formatDate(workflow.as_of)}`}
        actions={
          <Link className="button ghost" to="/workflows">
            返回列表
          </Link>
        }
      />
      {canStart || canResume ? (
        <section className="panel" style={{ marginBottom: "0.75rem" }}>
          <h3>{canStart ? "启动" : "恢复"}</h3>
          <div className="actions">
            {canStart ? (
              <button
                type="button"
                className="button primary"
                disabled={runMutation.isPending}
                onClick={() => runMutation.mutate()}
              >
                启动运行
              </button>
            ) : null}
            {canResume ? (
              <>
                <button
                  type="button"
                  className="button primary"
                  onClick={() =>
                    setConfirm({
                      mode: "resume",
                      title: "确认恢复工作流",
                      message: "恢复操作会沿用后端预算、节点和状态约束。",
                      showResume: true,
                      defaultComment: "admin-ui-resume",
                    })
                  }
                >
                  恢复运行
                </button>
                <button
                  type="button"
                  className="button danger"
                  onClick={() =>
                    setConfirm({
                      mode: "fact",
                      title: "确认降级为事实卡片",
                      message: "将强制以 fact_only 模式继续并可能缩短分析深度。",
                      danger: true,
                      defaultComment: "admin-ui-fact-only",
                    })
                  }
                >
                  降级事实卡片
                </button>
              </>
            ) : null}
          </div>
        </section>
      ) : null}
      <div className="split">
        <section className="panel">
          <h3>节点时间线</h3>
          <div className="timeline">
            {NODES.map((node) => {
              const nodeAttempts = attempts.filter((item) => item.node_name === node);
              const latest = nodeAttempts[nodeAttempts.length - 1];
              const status =
                workflow.current_node === node
                  ? workflow.status
                  : latest?.status || (nodeAttempts.length ? "succeeded" : "pending");
              return (
                <div key={node} className="timeline-item">
                  <div className="mono">{node}</div>
                  <div>
                    <StatusBadge value={status} />
                    {latest?.error_code ? (
                      <div className="muted">{latest.error_code}</div>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
        <section className="panel">
          <h3>预算用量</h3>
          {Object.keys(settled).length ? (
            Object.entries(settled).map(([dimension, amount]) => (
              <div key={dimension} style={{ marginBottom: "0.75rem" }}>
                <div className="muted">
                  {dimension}: {amount}
                </div>
                <div className="budget-bar">
                  <i style={{ width: `${Math.min(100, amount)}%` }} />
                </div>
              </div>
            ))
          ) : (
            <EmptyState>无预算记录</EmptyState>
          )}
        </section>
      </div>
      <section className="panel" style={{ marginTop: "0.75rem" }}>
        <div className="actions">
          <h3 style={{ margin: 0, flex: 1 }}>Blackboard（高级调试）</h3>
          <button type="button" className="button ghost" onClick={() => setShowBoard((v) => !v)}>
            {showBoard ? "收起" : "展开"}
          </button>
        </div>
        {showBoard ? <pre className="pre">{JSON.stringify(workflow.blackboard, null, 2)}</pre> : null}
      </section>
      <ConfirmDialog
        open={Boolean(confirm)}
        config={confirm}
        onCancel={() => setConfirm(null)}
        onConfirm={async ({ comment, resumeFrom }) => {
          if (!confirm) return;
          if (confirm.mode === "fact") {
            await resumeMutation.mutateAsync({
              trigger: "downgrade_fact_only",
              force_fact_only: true,
              reason: comment,
            });
          } else {
            await resumeMutation.mutateAsync({
              trigger: "budget_resume",
              resume_from: resumeFrom || null,
              budget_adjust: { model_calls: 10, tool_calls: 20 },
              reason: comment,
            });
          }
          setConfirm(null);
        }}
      />
    </>
  );
}
