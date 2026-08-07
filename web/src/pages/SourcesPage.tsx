import { useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { useToast } from "@/components/Toast";
import { useAuth } from "@/app/AuthContext";
import { apiGet, apiPatch, apiPost } from "@/lib/api";
import { asList, formatDate } from "@/lib/format";
import { canManageSources } from "@/lib/roles";
import type { IngestRun, Source, SourceHealth } from "@/types/api";

const LICENSE_OPTIONS = [
  { value: "inherit", label: "继承等级" },
  { value: "full", label: "全文" },
  { value: "excerpt", label: "摘录" },
  { value: "entry_only", label: "仅条目" },
] as const;

const ICONS = {
  sync: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M17.65 6.35A7.95 7.95 0 1 0 19.73 14h-2.08a6 6 0 1 1-1.41-6.24L13 11h7V4l-2.35 2.35Z" />
    </svg>
  ),
  logs: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 4h16v16H4V4Zm2 3v2h12V7H6Zm0 4v2h12v-2H6Zm0 4v2h12v-2H6Z" />
    </svg>
  ),
  health: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 2 4 6v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V6l-8-4Zm0 2.2 6 3v5.8c0 4.4-2.5 8.4-6 9.8-3.5-1.4-6-5.4-6-9.8V7.2l6-3Zm-1 4h2v3h3v2h-3v3h-2v-3H8v-2h3V8Z" />
    </svg>
  ),
  power: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M13 3h-2v10h2V3Zm4.83 2.17-1.42 1.42A7 7 0 1 1 12 4.05V2.05a9 9 0 1 0 5.83 3.12Z" />
    </svg>
  ),
  spinner: (
    <svg className="spin" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 2a10 10 0 0 1 10 10h-2a8 8 0 0 0-8-8V2Z" />
    </svg>
  ),
} as const;

function FormField({
  label,
  name,
  type = "text",
  required,
  pattern,
  placeholder,
  defaultValue,
  min,
  max,
  children,
}: {
  label: string;
  name: string;
  type?: string;
  required?: boolean;
  pattern?: string;
  placeholder?: string;
  defaultValue?: string | number;
  min?: number;
  max?: number;
  children?: ReactNode;
}) {
  return (
    <div className="form-field">
      <label htmlFor={name}>{label}</label>
      {children ?? (
        <input
          id={name}
          name={name}
          type={type}
          required={required}
          pattern={pattern}
          placeholder={placeholder}
          defaultValue={defaultValue}
          min={min}
          max={max}
        />
      )}
    </div>
  );
}

export function SourcesPage() {
  const { role } = useAuth();
  const { push } = useToast();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [syncResult, setSyncResult] = useState<string>("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [healthId, setHealthId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["sources"],
    queryFn: () => apiGet<Source[] | { items: Source[] }>("/api/v1/sources"),
  });
  const sources = asList<Source>(query.data);
  const manage = canManageSources(role);

  const runsQuery = useQuery({
    queryKey: ["source-runs", expandedId],
    enabled: Boolean(expandedId),
    queryFn: () => apiGet<IngestRun[]>(`/api/v1/sources/${expandedId}/runs?limit=10`),
  });
  const runs = asList<IngestRun>(runsQuery.data);

  const healthQuery = useQuery({
    queryKey: ["source-health", healthId],
    enabled: Boolean(healthId),
    queryFn: () => apiGet<SourceHealth>(`/api/v1/sources/${healthId}/health`),
  });
  const health = healthQuery.data;

  const seedMutation = useMutation({
    mutationFn: () => apiPost<{ inserted: number }>("/api/v1/sources/seed", {}),
    onSuccess: async (data) => {
      push(`新增种子源：${data.inserted ?? 0}`);
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (error) => push(error instanceof Error ? error.message : "失败", "error"),
  });

  const syncMutation = useMutation({
    mutationFn: (id: string) => apiPost<Record<string, unknown>>(`/api/v1/sources/${id}/sync`, {}),
    onSuccess: async (data) => {
      setSyncResult(JSON.stringify(data, null, 2));
      push("来源同步完成");
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
      await queryClient.invalidateQueries({ queryKey: ["source-runs"] });
      await queryClient.invalidateQueries({ queryKey: ["source-health"] });
    },
    onError: (error) => push(error instanceof Error ? error.message : "同步失败", "error"),
  });

  const syncAllMutation = useMutation({
    mutationFn: () =>
      apiPost<{ synced: number; results: Record<string, unknown>[] }>("/api/v1/sources/sync-all", {}),
    onSuccess: async (data) => {
      setSyncResult(JSON.stringify(data, null, 2));
      push(`已同步 ${data.synced ?? 0} 个来源`);
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
      await queryClient.invalidateQueries({ queryKey: ["source-runs"] });
      await queryClient.invalidateQueries({ queryKey: ["source-health"] });
    },
    onError: (error) => push(error instanceof Error ? error.message : "全量同步失败", "error"),
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      apiPatch(`/api/v1/sources/${id}`, { status }),
    onSuccess: async (_data, vars) => {
      push(vars.status === "active" ? "来源已启用" : "来源已禁用");
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
      await queryClient.invalidateQueries({ queryKey: ["source-health"] });
    },
    onError: (error) => push(error instanceof Error ? error.message : "状态更新失败", "error"),
  });

  const intervalMutation = useMutation({
    mutationFn: ({ id, crawl_interval_seconds }: { id: string; crawl_interval_seconds: number }) =>
      apiPatch(`/api/v1/sources/${id}`, { crawl_interval_seconds }),
    onSuccess: async () => {
      push("采集间隔已更新");
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (error) => push(error instanceof Error ? error.message : "更新失败", "error"),
  });

  const licenseMutation = useMutation({
    mutationFn: ({ id, license }: { id: string; license: string }) =>
      apiPatch(`/api/v1/sources/${id}`, { license }),
    onSuccess: async () => {
      push("许可策略已更新");
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (error) => push(error instanceof Error ? error.message : "更新失败", "error"),
  });

  async function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await apiPost("/api/v1/sources", {
        code: String(data.get("code") || "").trim(),
        name: String(data.get("name") || "").trim(),
        trust_tier: data.get("trust_tier"),
        feed_url: String(data.get("feed_url") || "").trim(),
        allowed_domains: String(data.get("allowed_domains") || "")
          .split(/[,，\s]+/)
          .filter(Boolean),
        rate_limit_per_minute: Number(data.get("rate_limit_per_minute")) || 10,
        crawl_interval_seconds: Number(data.get("crawl_interval_seconds")) || 3600,
        license: String(data.get("license") || "inherit"),
      });
      push("来源已创建");
      setShowCreate(false);
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
    } catch (error) {
      push(error instanceof Error ? error.message : "创建失败", "error");
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Ingestion"
        title="来源健康"
        description="定时采集、手动同步、启停与运行日志。"
        actions={
          manage ? (
            <>
              <button
                type="button"
                className="button primary"
                onClick={() => syncAllMutation.mutate()}
                disabled={syncAllMutation.isPending}
              >
                同步全部
              </button>
              <button
                type="button"
                className="button ghost"
                onClick={() => seedMutation.mutate()}
                disabled={seedMutation.isPending}
              >
                {seedMutation.isPending ? "写入中..." : "写入种子源"}
              </button>
              <button type="button" className="button ghost" onClick={() => setShowCreate((v) => !v)}>
                新建来源
              </button>
            </>
          ) : null
        }
      />
      {showCreate ? (
        <form className="panel" onSubmit={onCreate} style={{ marginBottom: "0.75rem" }}>
          <div className="form-grid">
            <FormField label="编码" name="code" required pattern="[a-z0-9_-]+" placeholder="source_code" />
            <FormField label="名称" name="name" required placeholder="来源名称" />
            <FormField label="信任等级" name="trust_tier" defaultValue="A">
              <select id="trust_tier" name="trust_tier" defaultValue="A">
                <option value="S">S</option>
                <option value="A">A</option>
                <option value="B">B</option>
                <option value="C">C</option>
              </select>
            </FormField>
            <FormField label="Feed URL" name="feed_url" required placeholder="https://..." />
            <FormField label="允许域名" name="allowed_domains" required placeholder="example.com,example2.com" />
            <FormField
              label="每分钟限速"
              name="rate_limit_per_minute"
              type="number"
              min={1}
              max={600}
              defaultValue={10}
            />
            <FormField
              label="采集间隔（秒）"
              name="crawl_interval_seconds"
              type="number"
              min={60}
              max={86400}
              defaultValue={3600}
            />
            <FormField label="许可策略" name="license" defaultValue="inherit">
              <select id="license" name="license" defaultValue="inherit">
                {LICENSE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </FormField>
          </div>
          <div className="action-bar" style={{ marginTop: "0.75rem" }}>
            <button className="button primary" type="submit">
              创建
            </button>
            <button
              type="button"
              className="button ghost"
              onClick={() => setShowCreate(false)}
            >
              取消
            </button>
          </div>
        </form>
      ) : null}
      {syncResult ? (
        <section className="panel" style={{ marginBottom: "0.75rem" }}>
          <div className="panel-header">
            <h3>最近同步结果</h3>
            <button type="button" className="button ghost sm" onClick={() => setSyncResult("")}>
              清空
            </button>
          </div>
          <pre className="pre">{syncResult}</pre>
        </section>
      ) : null}
      {query.isLoading ? <Skeleton /> : null}
      {query.isError ? <ErrorState>来源列表加载失败</ErrorState> : null}
      {!query.isLoading && !sources.length ? <EmptyState>暂无来源</EmptyState> : null}
      <DataTable
        headers={["名称", "等级/适配器", "状态", "间隔", "许可", "最近成功", "失败", "操作"]}
        rows={sources.flatMap((source) => {
          const license = source.license || "inherit";
          const main = (
            <tr key={source.id}>
              <td>
                {source.name}
                <div className="muted mono">
                  {source.code} · {source.id}
                </div>
                <div className="muted">{source.feed_url}</div>
              </td>
              <td>
                <StatusBadge value={source.trust_tier} />{" "}
                <StatusBadge value={source.adapter_type || "rss"} />
              </td>
              <td>
                <StatusBadge value={source.status} />
              </td>
              <td>
                {manage ? (
                  <input
                    type="number"
                    min={60}
                    max={86400}
                    defaultValue={source.crawl_interval_seconds ?? 3600}
                    style={{ width: "5.5rem" }}
                    disabled={intervalMutation.isPending && intervalMutation.variables?.id === source.id}
                    title="采集间隔（秒），失焦后自动保存"
                    onBlur={(event) => {
                      const next = Number(event.target.value);
                      const current = source.crawl_interval_seconds ?? 3600;
                      if (!Number.isFinite(next) || next < 60 || next === current) return;
                      intervalMutation.mutate({ id: source.id, crawl_interval_seconds: next });
                    }}
                  />
                ) : (
                  <>{source.crawl_interval_seconds ?? 3600}s</>
                )}
              </td>
              <td>
                {manage ? (
                  <select
                    value={license}
                    onChange={(event) => {
                      const next = event.target.value;
                      if (next === license) return;
                      licenseMutation.mutate({ id: source.id, license: next });
                    }}
                    disabled={licenseMutation.isPending && licenseMutation.variables?.id === source.id}
                    title="许可策略"
                  >
                    {LICENSE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  LICENSE_OPTIONS.find((opt) => opt.value === license)?.label ?? license
                )}
              </td>
              <td>{formatDate(source.last_success_at)}</td>
              <td>
                {source.consecutive_failures ?? 0}
                <div className="muted">{source.last_error_code || ""}</div>
              </td>
              <td>
                {manage ? (
                  <div className="button-group">
                    <button
                      type="button"
                      className="button ghost icon-sm"
                      onClick={() => syncMutation.mutate(source.id)}
                      disabled={syncMutation.isPending && syncMutation.variables === source.id}
                      title="立即同步该来源"
                      aria-label="同步来源"
                    >
                      {syncMutation.isPending && syncMutation.variables === source.id
                        ? ICONS.spinner
                        : ICONS.sync}
                    </button>
                    <button
                      type="button"
                      className="button ghost icon-sm"
                      onClick={() =>
                        setExpandedId((current) => (current === source.id ? null : source.id))
                      }
                      title={expandedId === source.id ? "收起运行日志" : "查看运行日志"}
                      aria-label="运行日志"
                    >
                      {ICONS.logs}
                    </button>
                    <button
                      type="button"
                      className="button ghost icon-sm"
                      onClick={() =>
                        setHealthId((current) => (current === source.id ? null : source.id))
                      }
                      title={healthId === source.id ? "收起健康检查" : "查看健康检查"}
                      aria-label="健康检查"
                    >
                      {ICONS.health}
                    </button>
                    <button
                      type="button"
                      className={`button icon-sm ${
                        statusMutation.isPending && statusMutation.variables?.id === source.id
                          ? "ghost"
                          : source.status === "active"
                            ? "danger"
                            : "primary"
                      }`}
                      onClick={() =>
                        statusMutation.mutate({
                          id: source.id,
                          status: source.status === "active" ? "disabled" : "active",
                        })
                      }
                      disabled={statusMutation.isPending && statusMutation.variables?.id === source.id}
                      title={source.status === "active" ? "禁用来源" : "启用来源"}
                      aria-label={source.status === "active" ? "禁用来源" : "启用来源"}
                    >
                      {statusMutation.isPending && statusMutation.variables?.id === source.id
                        ? ICONS.spinner
                        : ICONS.power}
                    </button>
                  </div>
                ) : (
                  "–"
                )}
              </td>
            </tr>
          );
          const panels = [main];
          if (expandedId === source.id) {
            panels.push(
              <tr key={`${source.id}-runs`}>
                <td colSpan={8}>
                  {runsQuery.isLoading ? <Skeleton /> : null}
                  {runsQuery.isError ? <ErrorState>运行日志加载失败</ErrorState> : null}
                  {!runsQuery.isLoading && !runs.length ? (
                    <EmptyState>暂无采集记录</EmptyState>
                  ) : (
                    <table className="nested-table">
                      <thead>
                        <tr>
                          <th>开始</th>
                          <th>触发</th>
                          <th>状态</th>
                          <th>fetched</th>
                          <th>processed</th>
                          <th>quarantined</th>
                          <th>说明</th>
                        </tr>
                      </thead>
                      <tbody>
                        {runs.map((run) => (
                          <tr key={run.id}>
                            <td>{formatDate(run.started_at)}</td>
                            <td>
                              <StatusBadge value={run.trigger} />
                            </td>
                            <td>
                              <StatusBadge value={run.status} />
                            </td>
                            <td>{run.fetched}</td>
                            <td>{run.processed}</td>
                            <td>{run.quarantined}</td>
                            <td className="muted">{run.message || ""}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </td>
              </tr>
            );
          }
          if (healthId === source.id) {
            panels.push(
              <tr key={`${source.id}-health`}>
                <td colSpan={8}>
                  {healthQuery.isLoading ? <Skeleton /> : null}
                  {healthQuery.isError ? <ErrorState>健康检查加载失败</ErrorState> : null}
                  {health ? (
                    <div className="panel">
                      <h4>
                        健康状态：{<StatusBadge value={health.health} />}
                      </h4>
                      <p className="muted">
                        连续失败 {health.consecutive_failures} 次 · 最后成功{" "}
                        {formatDate(health.last_success_at)}
                      </p>
                      {health.last_run ? (
                        <p className="muted">
                          最近运行 {formatDate(health.last_run.started_at)} · 状态{" "}
                          <StatusBadge value={health.last_run.status} /> · fetched{" "}
                          {health.last_run.fetched} / processed {health.last_run.processed}
                        </p>
                      ) : null}
                      {health.recent_runs.length > 1 ? (
                        <>
                          <h5>最近运行</h5>
                          <table className="nested-table">
                            <thead>
                              <tr>
                                <th>开始</th>
                                <th>状态</th>
                                <th>fetched</th>
                                <th>processed</th>
                                <th>quarantined</th>
                              </tr>
                            </thead>
                            <tbody>
                              {health.recent_runs.map((run) => (
                                <tr key={run.id}>
                                  <td>{formatDate(run.started_at)}</td>
                                  <td>
                                    <StatusBadge value={run.status} />
                                  </td>
                                  <td>{run.fetched}</td>
                                  <td>{run.processed}</td>
                                  <td>{run.quarantined}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </>
                      ) : null}
                    </div>
                  ) : null}
                </td>
              </tr>
            );
          }
          return panels;
        })}
      />
    </>
  );
}
