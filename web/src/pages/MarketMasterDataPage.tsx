import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/app/AuthContext";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { apiGet, apiPost } from "@/lib/api";
import type {
  ImpactPortfolioTarget,
  ImpactTargetMapping,
  IndustryClassification,
  IndustryTaxonomy,
  MarketInstrument,
  MarketMasterDataImportRun,
} from "@/types/api";

export function MarketMasterDataPage() {
  const { role } = useAuth();
  const client = useQueryClient();
  const [targetId, setTargetId] = useState("");
  const [importMessage, setImportMessage] = useState("");
  const instruments = useQuery({
    queryKey: ["market-master-instruments"],
    queryFn: () => apiGet<MarketInstrument[]>("/api/v1/market/instruments"),
  });
  const taxonomies = useQuery({
    queryKey: ["market-master-taxonomies"],
    queryFn: () => apiGet<IndustryTaxonomy[]>("/api/v1/market/industry-taxonomies"),
  });
  const industries = useQuery({
    queryKey: ["market-master-industries"],
    queryFn: () => apiGet<IndustryClassification[]>("/api/v1/market/industry-classifications"),
  });
  const targets = useQuery({
    queryKey: ["impact-targets"],
    queryFn: () => apiGet<ImpactPortfolioTarget[]>("/api/v1/impact-targets"),
  });
  const mappings = useQuery({
    queryKey: ["impact-target-mappings"],
    queryFn: () => apiGet<ImpactTargetMapping[]>("/api/v1/market/impact-target-mappings"),
  });
  const imports = useQuery({
    queryKey: ["market-master-imports"],
    queryFn: () => apiGet<MarketMasterDataImportRun[]>("/api/v1/market/master-data/imports"),
  });
  const targetNames = useMemo(
    () => new Map((targets.data || []).map((item) => [item.id, item.canonical_name])),
    [targets.data],
  );
  const refresh = () => client.invalidateQueries({ queryKey: ["impact-target-mappings"] });
  const suggest = useMutation({
    mutationFn: () =>
      apiPost<ImpactTargetMapping[]>("/api/v1/market/impact-target-mappings/suggest", {
        target_id: targetId,
      }),
    onSuccess: refresh,
  });
  const transition = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "approved" | "rejected" | "retired" }) =>
      apiPost<ImpactTargetMapping>(`/api/v1/market/impact-target-mappings/${id}/transition`, {
        status,
        reason: status === "approved" ? "研究工作台人工复核通过" : "研究工作台人工复核处理",
      }),
    onSuccess: refresh,
  });
  const stageImport = useMutation({
    mutationFn: (payload: unknown) => apiPost<MarketMasterDataImportRun>("/api/v1/market/master-data/imports", payload),
    onSuccess: (run) => {
      setImportMessage(run.status === "rejected" ? `校验未通过：${run.errors.join("；")}` : `已暂存 ${run.standard} ${run.version}`);
      void client.invalidateQueries({ queryKey: ["market-master-imports"] });
      void client.invalidateQueries({ queryKey: ["market-master-taxonomies"] });
      void client.invalidateQueries({ queryKey: ["market-master-industries"] });
    },
    onError: () => setImportMessage("导入文件解析或提交失败"),
  });
  const publishImport = useMutation({
    mutationFn: (id: string) => apiPost<MarketMasterDataImportRun>(`/api/v1/market/master-data/imports/${id}/publish`, { reason: "主数据工作台复核发布" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["market-master-imports"] });
      void client.invalidateQueries({ queryKey: ["market-master-taxonomies"] });
      void client.invalidateQueries({ queryKey: ["market-master-industries"] });
    },
  });
  const loading = [instruments, taxonomies, industries, targets, mappings, imports].some((item) => item.isLoading);
  const failed = [instruments, taxonomies, industries, targets, mappings, imports].some((item) => item.isError);
  const canReview = role === "reviewer" || role === "admin";

  return <>
    <PageHeader eyebrow="Reference data governance" title="市场主数据" description="管理证券目录、行业分类及影响目标映射；只有审核通过且在有效期内的映射会进入市场展望。" />
    {loading ? <section className="panel"><Skeleton /></section> : null}
    {failed ? <ErrorState>市场主数据加载失败</ErrorState> : null}
    {!loading && !failed ? <>
      <section className="forecast-eval-metrics">
        <article className="panel"><span>有效标的</span><strong>{instruments.data?.length || 0}</strong><small>A/H/US 指数、ETF 与证券</small></article>
        <article className="panel"><span>分类版本</span><strong>{taxonomies.data?.length || 0}</strong><small>{taxonomies.data?.filter((item) => item.status === "published").length || 0} 个已发布</small></article>
        <article className="panel"><span>行业节点</span><strong>{industries.data?.length || 0}</strong><small>带稳定代码和别名</small></article>
        <article className="panel"><span>已批准映射</span><strong>{mappings.data?.filter((item) => item.status === "approved").length || 0}</strong><small>可进入预测因子</small></article>
      </section>
      <section className="panel">
        <div className="section-heading"><div><span className="eyebrow">Mapping workflow</span><h2>影响目标映射</h2><p>精确名称或别名只生成候选，审核后才生效。</p></div></div>
        <div className="market-master-actions">
          <select value={targetId} onChange={(event) => setTargetId(event.target.value)} aria-label="选择影响目标">
            <option value="">选择影响目标</option>
            {(targets.data || []).map((item) => <option key={item.id} value={item.id}>{item.canonical_name} · {item.target_type}</option>)}
          </select>
          <button className="button primary" type="button" disabled={!targetId || suggest.isPending} onClick={() => suggest.mutate()}>{suggest.isPending ? "生成中…" : "生成精确候选"}</button>
        </div>
        {mappings.data?.length ? <div className="table-scroll"><table><thead><tr><th>研究目标</th><th>映射对象</th><th>类型</th><th>置信度</th><th>来源</th><th>状态</th><th>操作</th></tr></thead><tbody>{mappings.data.map((item) => <tr key={item.id}><td>{targetNames.get(item.target_id) || item.target_id}</td><td className="mono">{item.mapping_code}</td><td>{item.mapping_type}</td><td>{Math.round(item.confidence * 100)}%</td><td>{item.source}</td><td><StatusBadge value={item.status} /></td><td>{item.status === "proposed" && canReview ? <><button className="button primary sm" type="button" onClick={() => transition.mutate({ id: item.id, status: "approved" })}>批准</button> <button className="button ghost sm" type="button" onClick={() => transition.mutate({ id: item.id, status: "rejected" })}>拒绝</button></> : item.status === "approved" && canReview ? <button className="button ghost sm" type="button" onClick={() => transition.mutate({ id: item.id, status: "retired" })}>退役</button> : "—"}</td></tr>)}</tbody></table></div> : <EmptyState>尚无映射记录</EmptyState>}
      </section>
      <section className="panel">
        <div className="section-heading"><div><span className="eyebrow">Versioned import</span><h2>行业分类快照导入</h2><p>上传完整 JSON 快照；平台先校验并暂存，审核发布后按 effective_from 无缝切换。</p></div>{role === "admin" ? <label className="button primary">选择 JSON<input type="file" accept="application/json,.json" hidden onChange={(event) => { const file = event.target.files?.[0]; if (!file) return; void file.text().then((content) => stageImport.mutate(JSON.parse(content))).catch(() => setImportMessage("JSON 文件格式无效")); event.currentTarget.value = ""; }} /></label> : null}</div>
        {importMessage ? <p className="muted">{importMessage}</p> : null}
        {imports.data?.length ? <div className="table-scroll"><table><thead><tr><th>分类标准</th><th>版本</th><th>来源</th><th>行业/成员</th><th>状态</th><th>校验</th><th>操作</th></tr></thead><tbody>{imports.data.map((item) => <tr key={item.id}><td>{item.standard}</td><td className="mono">{item.version}</td><td>{item.source}</td><td>{item.classification_count} / {item.membership_count}</td><td><StatusBadge value={item.status} /></td><td>{item.errors.length ? item.errors.join("；") : item.warnings.join("；") || "通过"}</td><td>{item.status === "validated" && canReview ? <button className="button primary sm" type="button" disabled={publishImport.isPending} onClick={() => publishImport.mutate(item.id)}>发布</button> : "—"}</td></tr>)}</tbody></table></div> : <EmptyState>尚无行业分类导入运行</EmptyState>}
      </section>
      <section className="panel"><div className="section-heading"><div><span className="eyebrow">Taxonomy</span><h2>行业分类</h2></div></div><div className="table-scroll"><table><thead><tr><th>行业代码</th><th>名称</th><th>层级</th><th>别名</th><th>分类版本</th></tr></thead><tbody>{(industries.data || []).map((item) => <tr key={item.id}><td className="mono">{item.code}</td><td>{item.name}</td><td>L{item.level}</td><td>{item.aliases.join("、") || "—"}</td><td className="mono">{item.taxonomy_id}</td></tr>)}</tbody></table></div></section>
    </> : null}
  </>;
}
