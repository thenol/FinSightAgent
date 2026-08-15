import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { useToast } from "@/components/Toast";
import { useAuth } from "@/app/AuthContext";
import { apiGet, apiPost } from "@/lib/api";
import { asList, formatDate, taskAge } from "@/lib/format";
import { canRunResearch } from "@/lib/roles";
import type {
  ResearchBlackboard,
  ResearchPlan,
  ResearchPlanListItem,
  ResearchTask,
} from "@/types/api";

export function ResearchPage() {
  const { planId } = useParams();
  if (planId) return <ResearchDetailPage planId={planId} />;
  return <ResearchListPage />;
}

function ResearchListPage() {
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: ["research-plans"],
    queryFn: () =>
      apiGet<ResearchPlanListItem[] | { items: ResearchPlanListItem[] }>(
        "/api/v1/research?limit=200",
      ),
  });
  const plans = asList<ResearchPlanListItem>(query.data);

  return (
    <>
      <PageHeader
        eyebrow="Research"
        title="动态研究"
        description="由 LLM Planner 生成的可执行研究计划与任务状态。"
      />
      {query.isLoading ? <Skeleton /> : null}
      {query.isError ? <ErrorState>研究计划列表加载失败</ErrorState> : null}
      {!query.isLoading && !plans.length ? <EmptyState>暂无研究计划</EmptyState> : null}
      <DataTable
        headers={["研究问题", "目标", "状态", "预算档位", "创建时间"]}
        rows={plans.map((plan) => (
          <tr
            key={plan.id}
            className="clickable"
            onClick={() => navigate(`/research/${plan.id}`)}
          >
            <td>{plan.question}</td>
            <td className="muted">{plan.objective}</td>
            <td>
              <StatusBadge value={plan.status} />
            </td>
            <td>{plan.budget_profile}</td>
            <td>{formatDate(plan.created_at)}</td>
          </tr>
        ))}
      />
    </>
  );
}

function ResearchDetailPage({ planId }: { planId: string }) {
  const { role } = useAuth();
  const { push } = useToast();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<"tasks" | "blackboard">("tasks");

  const planQuery = useQuery({
    queryKey: ["research-plan", planId],
    queryFn: () => apiGet<ResearchPlan>(`/api/v1/research/${encodeURIComponent(planId)}`),
  });
  const tasksQuery = useQuery({
    queryKey: ["research-tasks", planId],
    queryFn: () =>
      apiGet<ResearchTask[] | { items: ResearchTask[] }>(
        `/api/v1/research/${encodeURIComponent(planId)}/tasks`,
      ),
  });
  const blackboardQuery = useQuery({
    queryKey: ["research-blackboard", planId],
    queryFn: () =>
      apiGet<ResearchBlackboard>(
        `/api/v1/research/${encodeURIComponent(planId)}/blackboard`,
      ),
  });

  const executeMutation = useMutation({
    mutationFn: () =>
      apiPost<ResearchPlan>(
        `/api/v1/research/${encodeURIComponent(planId)}/execute`,
        {},
      ),
    onSuccess: async () => {
      push("研究计划已开始执行");
      await queryClient.invalidateQueries({ queryKey: ["research-plan", planId] });
      await queryClient.invalidateQueries({ queryKey: ["research-tasks", planId] });
      await queryClient.invalidateQueries({ queryKey: ["research-blackboard", planId] });
    },
    onError: (error) => push(error instanceof Error ? error.message : "执行失败", "error"),
  });

  if (planQuery.isLoading) return <Skeleton />;
  if (planQuery.isError || !planQuery.data) return <ErrorState>研究计划详情不可用</ErrorState>;

  const plan = planQuery.data;
  const tasks = asList<ResearchTask>(tasksQuery.data).sort(
    (a, b) => new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime(),
  );
  const canExecute = canRunResearch(role) && ["ready", "pending", "waiting_review"].includes(plan.status);

  return (
    <>
      <PageHeader
        eyebrow={plan.budget_profile}
        title={plan.question}
        description={`目标：${plan.objective} · 状态 ${plan.status} · as_of ${formatDate(plan.as_of)}`}
        actions={
          <div className="actions">
            <Link className="button ghost" to="/research">
              返回列表
            </Link>
            {canExecute ? (
              <button
                type="button"
                className="button primary"
                disabled={executeMutation.isPending}
                onClick={() => executeMutation.mutate()}
              >
                执行研究
              </button>
            ) : null}
          </div>
        }
      />

      <div className="tabs" style={{ marginBottom: "0.75rem" }}>
        <button
          type="button"
          className={`tab${activeTab === "tasks" ? " active" : ""}`}
          onClick={() => setActiveTab("tasks")}
        >
          任务时间线
        </button>
        <button
          type="button"
          className={`tab${activeTab === "blackboard" ? " active" : ""}`}
          onClick={() => setActiveTab("blackboard")}
        >
          研究黑板
        </button>
      </div>

      {activeTab === "tasks" ? (
        <section className="panel">
          <h3>任务时间线</h3>
          {tasks.length === 0 ? (
            <EmptyState>暂无任务</EmptyState>
          ) : (
            <div className="timeline">
              {tasks.map((task) => (
                <div key={task.id} className={`timeline-item ${task.status}`}>
                  <div className="timeline-marker">
                    <StatusBadge value={task.status} />
                  </div>
                  <div className="timeline-content">
                    <p className="timeline-title">{task.name}</p>
                    <p className="timeline-meta">
                      agent: <code>{task.agent_key}</code> · 依赖: {task.dependencies.join(", ") || "–"}
                    </p>
                    <p className="muted">{task.description}</p>
                    {task.output_snapshot ? (
                      <details className="timeline-output">
                        <summary>输出快照</summary>
                        <pre>{JSON.stringify(task.output_snapshot, null, 2)}</pre>
                      </details>
                    ) : null}
                    {task.review_reason ? (
                      <p className="timeline-note warn">审核原因：{task.review_reason}</p>
                    ) : null}
                    <p className="timeline-time">
                      耗时 {taskAge(task.started_at)} · 结束 {formatDate(task.ended_at)}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      ) : (
        <section className="panel">
          <h3>研究黑板</h3>
          {blackboardQuery.isLoading ? <Skeleton /> : null}
          {blackboardQuery.isError ? <ErrorState>黑板加载失败</ErrorState> : null}
          {blackboardQuery.data ? (
            <>
              <h4>研究计划</h4>
              <pre className="code-block">
                {JSON.stringify(blackboardQuery.data.research_plan, null, 2)}
              </pre>
              <h4 style={{ marginTop: "1rem" }}>任务输出</h4>
              <pre className="code-block">
                {JSON.stringify(blackboardQuery.data.task_outputs, null, 2)}
              </pre>
            </>
          ) : null}
        </section>
      )}
    </>
  );
}
