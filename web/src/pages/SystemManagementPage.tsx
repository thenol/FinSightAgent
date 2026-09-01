import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { ConfirmDialog, type ConfirmConfig } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { apiGet, apiPatch, apiPost } from "@/lib/api";
import type { SourceCollectionControl, SystemStatus } from "@/types/api";

export function SystemManagementPage() {
  const { push } = useToast();
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState<(ConfirmConfig & { action: string }) | null>(null);
  const query = useQuery({ queryKey: ["system-status"], queryFn: () => apiGet<SystemStatus>("/api/v1/admin/system/status"), refetchInterval: 30_000 });
  const collectionQuery = useQuery({ queryKey: ["collection-control"], queryFn: () => apiGet<SourceCollectionControl>("/api/v1/admin/system/collection"), refetchInterval: 30_000 });
  const collectionMutation = useMutation({
    mutationFn: (scheduler_enabled: boolean) => apiPatch("/api/v1/admin/system/collection", { scheduler_enabled }),
    onSuccess: async () => { push("自动采集配置已更新"); await collectionQuery.refetch(); await queryClient.invalidateQueries({ queryKey: ["system-status"] }); },
    onError: (error) => push(error instanceof Error ? error.message : "采集配置更新失败", "error"),
  });
  const actionMutation = useMutation({
    mutationFn: (action: string) => apiPost(`/api/v1/admin/system/actions/${action}`, action === "retry-outbox" ? { retry_all_dead: true } : {}),
    onSuccess: async (_data, action) => { push(action === "sync-sources" ? "来源同步已完成" : action === "retry-outbox" ? "死信消息已重新入队" : "影响投影已重建"); await queryClient.invalidateQueries({ queryKey: ["system-status"] }); },
    onError: (error) => push(error instanceof Error ? error.message : "系统操作失败", "error"),
  });
  const status = query.data;
  if (query.isLoading) return <Skeleton />;
  if (query.isError || !status) return <ErrorState>系统状态加载失败</ErrorState>;
  const collection = collectionQuery.data;
  const ask = (action: string, title: string, message: string, danger = false) => setConfirm({ action, title, message, submitLabel: "确认执行", danger, commentRequired: false });
  return <>
    <PageHeader eyebrow="Platform operations" title="系统管理" description="查看平台、依赖、Worker 与队列状态；执行受控的应用级修复操作。" actions={<button type="button" className="button ghost" onClick={() => query.refetch()}>刷新状态</button>} />
    <section className="metric-strip">
      <Metric label="API" value={status.platform.api_status} status={status.platform.api_status} />
      <Metric label="数据库" value={status.dependencies.database.status} status={status.dependencies.database.status} />
      <Metric label="Redis" value={status.dependencies.redis.status} status={status.dependencies.redis.status} />
      <Metric label="活动工作流" value={String(status.queues.active_workflows)} />
    </section>
    <div className="split">
      <section className="panel"><h3>平台与配置</h3><Field label="环境" value={status.platform.environment} /><Field label="数据仓储" value={status.platform.repository} /><Field label="市场数据" value={status.configuration.market_data_provider} /><Field label="审核策略" value={status.configuration.review_policy === "agent" ? "Agent 自动审核" : "人工审核"} /><Field label="活动来源" value={String(status.configuration.active_sources)} /><Field label="模型接口" value={String(status.configuration.configured_llm_providers)} /><p className="muted">启动：<code>./scripts/start.sh --dev</code>（开发）或 <code>./scripts/start.sh --build</code>（Compose 构建启动）。</p></section>
      <section className="panel"><h3>运行队列</h3><Field label="Outbox 待处理" value={String(status.queues.outbox_pending)} /><Field label="Outbox 死信" value={String(status.queues.outbox_dead_lettered)} /><Field label="待审核" value={String(status.queues.pending_reviews)} /><Field label="隔离项" value={String(status.queues.open_quarantine)} /></section>
    </div>
    <section className="panel" style={{ marginTop: "0.75rem" }}><h3>Worker 心跳</h3>{status.workers.length ? <div className="system-worker-grid">{status.workers.map((worker) => <article key={worker.name} className="system-worker"><div><strong>{worker.name}</strong><p className="muted">{worker.last_heartbeat_at ? `最近心跳 ${new Date(worker.last_heartbeat_at).toLocaleString("zh-CN")}` : "尚未上报心跳"}</p></div><StatusBadge value={worker.status} /></article>)}</div> : <EmptyState>暂无 Worker 状态</EmptyState>}</section>
    {collection && <section className="panel" style={{ marginTop: "0.75rem" }}><div className="panel-heading"><div><h3>自动采集中心</h3><p className="muted">调度策略与来源健康度（不直接控制宿主机进程）。</p></div><StatusBadge value={collection.config.scheduler_enabled ? "running" : "paused"} /></div><div className="metric-strip compact"><Metric label="活动来源" value={String(collection.sources.active)} /><Metric label="异常来源" value={String(collection.sources.degraded)} /><Metric label="已禁用" value={String(collection.sources.disabled)} /><Metric label="默认间隔" value={`${Math.round(collection.config.default_crawl_interval_seconds / 60)} 分钟`} /></div><div className="actions"><button type="button" className="button ghost" onClick={() => collectionMutation.mutate(!collection.config.scheduler_enabled)} disabled={collectionMutation.isPending}>{collection.config.scheduler_enabled ? "暂停自动采集" : "恢复自动采集"}</button></div><div className="table-wrap"><table><thead><tr><th>来源</th><th>状态</th><th>最近成功</th><th>连续失败</th><th>错误</th></tr></thead><tbody>{collection.health.map((source) => <tr key={source.id}><td><strong>{source.name}</strong><br /><span className="muted">{source.code}</span></td><td><StatusBadge value={source.status} /></td><td>{source.last_success_at ? new Date(source.last_success_at).toLocaleString("zh-CN") : "—"}</td><td>{source.consecutive_failures}</td><td>{source.last_error_code || "—"}</td></tr>)}</tbody></table></div></section>}
    <section className="panel" style={{ marginTop: "0.75rem" }}><h3>受控运维操作</h3><p className="muted">不会启动、停止或重启宿主机服务；所有操作均记录审计日志。</p><div className="actions"><button type="button" className="button ghost" onClick={() => ask("sync-sources", "同步活动来源", "将立即同步所有活动来源，可能耗时较长。")} disabled={actionMutation.isPending}>同步活动来源</button><button type="button" className="button ghost" onClick={() => ask("retry-outbox", "重试死信消息", "最多将 100 条死信 Outbox 消息重新放入重试队列。") } disabled={actionMutation.isPending || !status.queues.outbox_dead_lettered}>重试死信</button><button type="button" className="button primary" onClick={() => ask("rebuild-impact-projections", "重建影响投影", "仅重算已批准分析的影响贡献与快照，不修改研究结论。", true)} disabled={actionMutation.isPending}>重建影响投影</button></div></section>
    <ConfirmDialog open={Boolean(confirm)} config={confirm} onCancel={() => setConfirm(null)} onConfirm={async () => { if (!confirm) return; await actionMutation.mutateAsync(confirm.action); setConfirm(null); }} />
  </>;
}

function Metric({ label, value, status }: { label: string; value: string; status?: string }) { return <article className="metric-card"><span>{label}</span><strong>{status ? <StatusBadge value={value} /> : value}</strong></article>; }
function Field({ label, value }: { label: string; value: string }) { return <p><span className="muted">{label}</span><br /><strong>{value}</strong></p>; }
