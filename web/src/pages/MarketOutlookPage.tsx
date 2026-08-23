import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { apiGet } from "@/lib/api";
import type { MarketInstrument, MarketOutlook } from "@/types/api";

const HORIZONS = [1, 3, 5, 20] as const;
const LOOKBACK_DAYS: Record<number, number> = { 1: 120, 3: 180, 5: 240, 20: 500 };
const labels: Record<string, string> = { up: "上涨", flat: "震荡", down: "下跌" };
const directionLabels: Record<string, string> = { positive: "偏多", mixed: "中性", negative: "偏空", unknown: "数据不足" };

function dateTime(days: number): string {
  const value = new Date();
  value.setUTCDate(value.getUTCDate() - days);
  return value.toISOString();
}

export function MarketOutlookPage() {
  const [market, setMarket] = useState<"cn" | "hk" | "us">("cn");
  const [horizon, setHorizon] = useState<number>(1);
  const instrumentsQuery = useQuery({
    queryKey: ["market-instruments", market],
    queryFn: () => apiGet<MarketInstrument[]>(`/api/v1/market/instruments?market=${market}`),
  });
  const instruments = instrumentsQuery.data || [];
  const ids = useMemo(() => instruments.filter((item) => item.instrument_type === "index" || item.instrument_type === "etf").map((item) => item.id), [instruments]);
  const outlookQuery = useQuery({
    queryKey: ["market-outlooks", market, ids.join(","), horizon],
    enabled: ids.length > 0,
    queryFn: () => {
      const params = new URLSearchParams({ start: dateTime(LOOKBACK_DAYS[horizon]), end: new Date().toISOString(), horizon: String(horizon), interval: "1d", limit: "500" });
      ids.forEach((id) => params.append("instrument_ids", id));
      return apiGet<MarketOutlook[]>(`/api/v1/market/outlooks?${params.toString()}`);
    },
  });
  const byId = new Map(instruments.map((item) => [item.id, item]));
  return <>
    <PageHeader eyebrow="Market outlook" title="市场展望" description="基于可回放行情状态生成多市场方向概率；每项结论均展示数据质量、贡献拆解和校准状态。" />
    <section className="market-outlook-toolbar" aria-label="市场展望筛选">
      <div className="market-outlook-segmented">{(["cn", "hk", "us"] as const).map((item) => <button type="button" key={item} className={market === item ? "is-active" : ""} onClick={() => setMarket(item)}>{item === "cn" ? "A股" : item === "hk" ? "港股" : "美股"}</button>)}</div>
      <div className="market-outlook-segmented">{HORIZONS.map((item) => <button type="button" key={item} className={horizon === item ? "is-active" : ""} onClick={() => setHorizon(item)}>{item}日</button>)}</div>
      <span className="market-outlook-note">研究窗口：近 {LOOKBACK_DAYS[horizon]} 个自然日 · 日线</span>
    </section>
    <details className="market-outlook-methodology">
      <summary>如何计算这些概率？</summary>
      <div className="market-outlook-methodology__body">
        <p>系统先用近期价格行为计算“市场状态”分数，再与事件影响、预期差和已定价程度合成方向分数。</p>
        <div className="market-outlook-methodology__grid">
          <div><strong>市场状态 · 45%</strong><span>短期（近 5 个收盘价）相对中期（近 20 个收盘价）的强弱。</span></div>
          <div><strong>事件影响 · 25%</strong><span>仅使用截止时点已批准、且与标的显式映射的事件影响。</span></div>
          <div><strong>预期差 · 20%</strong><span>实际信息相对市场预期的超预期或低于预期程度。</span></div>
          <div><strong>已定价程度 · 10%</strong><span>信息是否已经提前反映在价格中；尚未接入时退出计算。</span></div>
        </div>
        <p className="market-outlook-methodology__formula">配置权重为：市场状态×45% + 事件影响×25% + 预期差×20% + 已定价程度×10%。缺失因子不会按 0 分冒充中性，而是退出本次计算，其余可用因子按配置权重同比例归一化。</p>
        <p className="market-outlook-methodology__note">当前版本是可解释基线，状态标记为“基线未校准”；数据不足或过期时不会生成方向性结论。</p>
      </div>
    </details>
    {outlookQuery.isError ? <ErrorState>市场展望加载失败，请检查行情供应商状态</ErrorState> : null}
    {instrumentsQuery.isLoading || outlookQuery.isLoading ? <div className="panel"><Skeleton /></div> : null}
    {!outlookQuery.isLoading && !outlookQuery.isError && outlookQuery.data?.length === 0 ? <EmptyState>当前市场暂无可用展望</EmptyState> : null}
    <section className="market-outlook-grid">
      {(outlookQuery.data || []).map((item) => <OutlookCard key={item.instrument_id} outlook={item} instrument={byId.get(item.instrument_id)} />)}
    </section>
  </>;
}

function OutlookCard({ outlook, instrument }: { outlook: MarketOutlook; instrument?: MarketInstrument }) {
  const probability = outlook.probabilities;
  const hasForecast = probability != null && outlook.forecast_status !== "insufficient_data";
  return <article className={`panel market-outlook-card is-${outlook.direction}`}>
    <header className="market-outlook-card__header"><div><span className="eyebrow">{instrument?.exchange || "Market"} · {instrument?.instrument_type || "instrument"}</span><h2>{instrument?.name || outlook.instrument_id}</h2><p className="muted">{outlook.instrument_id} · 截止 {new Date(outlook.as_of).toLocaleString("zh-CN")}</p></div><strong className="market-outlook-direction">{directionLabels[outlook.direction]}</strong></header>
    {!hasForecast ? <div className="market-outlook-no-conclusion"><strong>暂无方向性结论</strong><span>已有 {outlook.available_observations} 个交易日，当前周期至少需要 {outlook.required_observations} 个；系统不会用占位概率代替预测。</span>{outlook.blocking_reasons.length ? <small>{outlook.blocking_reasons.join(" · ")}</small> : null}</div> : <div className="market-outlook-probabilities">{Object.entries(probability).map(([key, value]) => <div key={key}><div className="market-outlook-probability-label"><span>{labels[key]}</span><strong>{(value * 100).toFixed(1)}%</strong></div><div className={`market-outlook-bar is-${key}`}><i style={{ width: `${value * 100}%` }} /></div></div>)}</div>}
    <div className="market-outlook-quality"><span>样本 {outlook.available_observations}/{outlook.required_observations}</span><span>行情覆盖 {(outlook.coverage * 100).toFixed(0)}%</span><span>因子覆盖 {(outlook.factor_coverage * 100).toFixed(0)}%</span><span>最近行情 {outlook.latest_observed_at ? new Date(outlook.latest_observed_at).toLocaleDateString("zh-CN") : "—"}</span></div>
    <div className="market-outlook-return"><span>预期收益区间</span><strong>{outlook.expected_return_p50 == null ? "—" : `${(outlook.expected_return_p10! * 100).toFixed(1)}% ~ ${(outlook.expected_return_p90! * 100).toFixed(1)}%`}</strong><small>中位数 {outlook.expected_return_p50 == null ? "—" : `${(outlook.expected_return_p50 * 100).toFixed(1)}%`} · 置信度 {(outlook.confidence * 100).toFixed(0)}%</small></div>
    <div className="market-outlook-contributions"><div className="market-outlook-section-title">贡献拆解</div>{outlook.contributions.map((item) => <div className={`market-outlook-contribution ${item.status !== "available" ? "is-unavailable" : ""}`} key={item.source} title={item.explanation}><span>{item.source === "market_state" ? "市场状态" : item.source === "expectation_gap" ? "预期差" : item.source === "priced_in" ? "已定价程度" : "事件影响"}</span><i><b style={{ width: `${Math.abs(item.score) * 100}%` }} /></i><strong>{item.status === "available" ? `${item.score > 0 ? "+" : ""}${item.score.toFixed(2)}` : "未接入"}</strong>{item.source === "event" && item.provenance.sources?.length ? <small>{item.provenance.sources.map((source) => <Link key={source.target_id} to={`/impact-targets/${encodeURIComponent(source.target_id)}`}>{source.target_name}</Link>)}</small> : item.source === "event" && item.provenance.reason ? <small>{item.provenance.reason === "impact_target_not_mapped" ? "尚未建立影响目标映射" : "暂无有效的已批准影响快照"}</small> : null}</div>)}</div>
    <footer className="market-outlook-card__footer"><span className={`status-chip ${outlook.forecast_status === "uncalibrated" ? "warn" : outlook.forecast_status === "ready" ? "ok" : ""}`}>{outlook.forecast_status === "uncalibrated" ? "基线未校准" : outlook.forecast_status === "insufficient_data" ? "数据不足" : outlook.forecast_status === "ready" ? "已校准" : outlook.forecast_status}</span><span>{outlook.risks[0]}</span></footer>
  </article>;
}
