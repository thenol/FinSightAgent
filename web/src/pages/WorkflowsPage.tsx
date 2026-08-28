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

const NODES = ["context", "fact_check", "preliminary_assess", "company", "skeptic", "synthesize", "draft", "guardrail"];

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
        headers={["研究主题", "状态", "当前阶段", "进度", "最近活动", "问题"]}
        rows={workflows.map((workflow) => (
          <tr
            key={workflow.id}
            className="clickable"
            onClick={() => navigate(`/workflows/${workflow.id}`)}
          >
            <td title={workflow.display?.event_title || workflow.display?.title || workflow.id}><div className="workflow-display-title">{workflow.display?.title || "研究工作流"}</div><div className="muted workflow-display-subtitle">{workflow.display?.subtitle || "待获取研究主题"} · <span className="mono">{workflow.display?.short_id || workflow.id}</span></div></td>
            <td>
              <StatusBadge value={workflow.status} />
            </td>
            <td>{workflow.display?.current_stage_label || workflow.current_node || "等待启动"}</td>
            <td><div className="workflow-progress"><i style={{ width: `${workflow.display?.progress_percent ?? 0}%` }} /></div><small>{workflow.display?.progress_percent ?? 0}% · v{workflow.state_version}</small></td>
            <td>{workflow.display?.last_activity_at ? `${formatDate(workflow.display.last_activity_at)}` : "–"}</td>
            <td>{workflow.display?.error_label || "–"}</td>
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
  const visibleNodes = NODES.filter(
    (node) =>
      node !== "preliminary_assess" ||
      attempts.some(
        (item) => item.node_name === node || item.node_name === `dynamic:${node}`,
      ) ||
      Object.prototype.hasOwnProperty.call(workflow.blackboard || {}, "preliminary_assessment"),
  );

  return (
    <>
      <PageHeader
        eyebrow={workflow.budget_profile || "workflow"}
        title={workflow.display?.title || "研究工作流"}
        description={`${workflow.display?.subtitle || "研究工作流"} · ${workflow.display?.current_stage_label || workflow.current_node || "等待启动"} · as_of ${formatDate(workflow.as_of)}`}
        actions={
          <div className="actions">
            {workflow.display?.event_href ? <Link className="button ghost" to={workflow.display.event_href}>查看事件</Link> : null}
            <Link className="button ghost" to="/workflows">返回列表</Link>
          </div>
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
            {visibleNodes.map((node) => {
              const nodeAttempts = attempts.filter((item) => item.node_name === node);
              const latest = nodeAttempts[nodeAttempts.length - 1];
              const status =
                workflow.current_node === node
                  ? workflow.status
                  : latest?.status || (nodeAttempts.length ? "succeeded" : "pending");
              const label = workflow.display?.current_stage_label && node === workflow.current_node ? workflow.display.current_stage_label : nodeLabel(node);
              return (
                <div key={node} className="timeline-item">
                  <div><strong>{label}</strong><small className="muted mono">{node}</small></div>
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

function nodeLabel(node: string): string {
  const labels: Record<string, string> = { context: "构建研究上下文", fact_check: "核验事件事实", preliminary_assess: "事件初步研判", company: "公司影响分析", industry: "行业传导分析", market: "市场反应分析", skeptic: "反方审查", synthesize: "综合研究结论", draft: "生成报告草稿", guardrail: "质量与合规检查" };
  return labels[node] || node;
}
