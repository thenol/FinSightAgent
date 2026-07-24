import { useState, type FormEvent } from "react";
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
import type { IngestRun, Source } from "@/types/api";

const LICENSE_OPTIONS = [
  { value: "inherit", label: "继承等级" },
  { value: "full", label: "全文" },
  { value: "excerpt", label: "摘录" },
  { value: "entry_only", label: "仅条目" },
] as const;

export function SourcesPage() {
  const { role } = useAuth();
  const { push } = useToast();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [syncResult, setSyncResult] = useState<string>("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

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
    },
    onError: (error) => push(error instanceof Error ? error.message : "全量同步失败", "error"),
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      apiPatch(`/api/v1/sources/${id}`, { status }),
    onSuccess: async (_data, vars) => {
      push(vars.status === "active" ? "来源已启用" : "来源已禁用");
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
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
              <button type="button" className="button ghost" onClick={() => seedMutation.mutate()}>
                写入种子源
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
          <div className="form-row">
            <input name="code" required pattern="[a-z0-9_-]+" placeholder="code" />
            <input name="name" required placeholder="名称" />
            <select name="trust_tier" defaultValue="A">
              <option>S</option>
              <option>A</option>
              <option>B</option>
              <option>C</option>
            </select>
            <input name="feed_url" required placeholder="feed_url" />
            <input name="allowed_domains" required placeholder="domains" />
            <input name="rate_limit_per_minute" type="number" min={1} max={600} defaultValue={10} />
            <input
              name="crawl_interval_seconds"
              type="number"
              min={60}
              max={86400}
              defaultValue={3600}
              placeholder="interval(s)"
            />
            <select name="license" defaultValue="inherit" title="许可策略">
              {LICENSE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            <button className="button primary" type="submit">
              创建
            </button>
          </div>
        </form>
      ) : null}
      {syncResult ? (
        <section className="panel" style={{ marginBottom: "0.75rem" }}>
          <h3>最近同步结果</h3>
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
                    disabled={licenseMutation.isPending}
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
                  <div className="actions">
                    <button
                      type="button"
                      className="button ghost"
                      onClick={() => syncMutation.mutate(source.id)}
                    >
                      同步
                    </button>
                    <button
                      type="button"
                      className="button ghost"
                      onClick={() =>
                        setExpandedId((current) => (current === source.id ? null : source.id))
                      }
                    >
                      {expandedId === source.id ? "收起日志" : "运行日志"}
                    </button>
                    <button
                      type="button"
                      className="button ghost"
                      onClick={() =>
                        statusMutation.mutate({
                          id: source.id,
                          status: source.status === "active" ? "disabled" : "active",
                        })
                      }
                    >
                      {source.status === "active" ? "禁用" : "启用"}
                    </button>
                  </div>
                ) : (
                  "–"
                )}
              </td>
            </tr>
          );
          if (expandedId !== source.id) return [main];
          return [
            main,
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
            </tr>,
          ];
        })}
      />
    </>
  );
}
