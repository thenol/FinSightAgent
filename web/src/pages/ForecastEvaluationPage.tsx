import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/app/AuthContext";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { apiGet, apiPost } from "@/lib/api";
import type { ChampionChallengerComparison, HistoricalForecastReplayReceipt, MarketCalibrationVersion, MarketForecastEvaluation, MarketInstrument } from "@/types/api";

const HORIZONS = [1, 3, 5, 20] as const;
const DEFAULT_INSTRUMENT = { cn: "cn:index:000300", hk: "hk:index:HSI", us: "us:index:SPX" } as const;

function isoDate(daysAgo: number): string {
  const value = new Date();
  value.setDate(value.getDate() - daysAgo);
  return value.toISOString().slice(0, 10);
}

function percent(value: number | null): string {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function decimal(value: number | null): string {
  return value == null ? "—" : value.toFixed(3);
}

export function ForecastEvaluationPage() {
  const { role } = useAuth();
  const queryClient = useQueryClient();
  const [market, setMarket] = useState<"cn" | "hk" | "us">("cn");
  const [horizon, setHorizon] = useState(1);
  const [instrumentId, setInstrumentId] = useState<string>(DEFAULT_INSTRUMENT.cn);
  const [replayFrom, setReplayFrom] = useState(isoDate(90));
  const [replayTo, setReplayTo] = useState(isoDate(5));
  const instruments = useQuery({
    queryKey: ["market-instruments", market],
    queryFn: () => apiGet<MarketInstrument[]>(`/api/v1/market/instruments?market=${market}`),
  });
  const evaluation = useQuery({
    queryKey: ["market-evaluation", market, horizon],
    queryFn: () => apiGet<MarketForecastEvaluation>(`/api/v1/market/evaluations?market=${market}&horizon=${horizon}`),
  });
  const calibrations = useQuery({
    queryKey: ["market-calibrations", market, horizon],
    queryFn: () => apiGet<MarketCalibrationVersion[]>(`/api/v1/market/calibrations?market=${market}&horizon=${horizon}`),
  });
  const comparison = useQuery({
    queryKey: ["market-model-comparison", market, horizon],
    queryFn: () => apiGet<ChampionChallengerComparison>(`/api/v1/market/model-comparisons?market=${market}&horizon=${horizon}`),
  });
  const report = evaluation.data?.report;
  const usefulBins = report?.calibration_bins.filter((item) => item.count > 0) || [];
  const replay = useMutation({
    mutationFn: () => apiPost<HistoricalForecastReplayReceipt>("/api/v1/market/forecast-replays", {
      instrument_ids: [instrumentId],
      forecast_from: replayFrom,
      forecast_to: replayTo,
      horizon,
      lookback_days: Math.max(500, horizon * 30),
      publication_lag_minutes: 30,
      max_slots: 5000,
      settle_outcomes: true,
    }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["market-evaluation", market, horizon] }),
        queryClient.invalidateQueries({ queryKey: ["market-model-comparison", market, horizon] }),
      ]);
    },
  });

  function selectMarket(value: "cn" | "hk" | "us") {
    setMarket(value);
    setInstrumentId(DEFAULT_INSTRUMENT[value]);
  }

  return <>
    <PageHeader eyebrow="Forecast governance" title="预测评估" description="持续检验方向概率是否可信；覆盖率、准确率和校准误差均来自已固化预测与到期真实结果。" />
    <section className="market-outlook-toolbar" aria-label="预测评估筛选">
      <div className="market-outlook-segmented">{(["cn", "hk", "us"] as const).map((item) => <button type="button" key={item} className={market === item ? "is-active" : ""} onClick={() => selectMarket(item)}>{item === "cn" ? "A股" : item === "hk" ? "港股" : "美股"}</button>)}</div>
      <div className="market-outlook-segmented">{HORIZONS.map((item) => <button type="button" key={item} className={horizon === item ? "is-active" : ""} onClick={() => setHorizon(item)}>{item}日</button>)}</div>
      <span className="market-outlook-note">评估口径：三分类 · return-band-v1</span>
    </section>

    {role === "admin" ? <section className="panel forecast-replay-panel"><div className="section-heading"><div><span className="eyebrow">Point-in-time replay</span><h2>历史预测数据集回放</h2><p>仅从通过完整性校验的不可变行情归档读取；按当时可知信息生成预测，并用归档中的后续交易日自动结算。</p></div><StatusBadge value={replay.data?.status || "ready"} /></div>
      <div className="form-grid">
        <div className="form-field"><label htmlFor="replay-instrument">标的</label><select id="replay-instrument" value={instrumentId} onChange={(event) => setInstrumentId(event.target.value)}>{instruments.data?.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.symbol}</option>)}</select></div>
        <div className="form-field"><label htmlFor="replay-from">预测开始日</label><input id="replay-from" type="date" value={replayFrom} onChange={(event) => setReplayFrom(event.target.value)} /></div>
        <div className="form-field"><label htmlFor="replay-to">预测结束日</label><input id="replay-to" type="date" value={replayTo} onChange={(event) => setReplayTo(event.target.value)} /></div>
        <div className="form-field"><label>执行</label><button className="button primary" type="button" disabled={replay.isPending || !instrumentId || !replayFrom || !replayTo} onClick={() => replay.mutate()}>{replay.isPending ? "回放中…" : `回放并结算 ${horizon} 日预测`}</button></div>
      </div>
      {replay.isError ? <p className="error-text">回放失败：请检查日期范围、归档完整性和最大任务规模。</p> : null}
      {replay.data ? <div className="forecast-replay-summary"><span>计划槽位<strong>{replay.data.scheduled_slots}</strong></span><span>新建 / 复用<strong>{replay.data.created_count} / {replay.data.reused_count}</strong></span><span>已结算 / 待结算<strong>{replay.data.settled_count} / {replay.data.pending_outcome_count}</strong></span><span>数据不足<strong>{replay.data.insufficient_count}</strong></span><span>归档源<strong>{replay.data.source_provider}</strong></span></div> : null}
      {replay.data?.warnings.length ? <small className="muted">提示：{replay.data.warnings.join("；")}</small> : null}
    </section> : null}

    {evaluation.isError || calibrations.isError ? <ErrorState>预测评估数据加载失败</ErrorState> : null}
    {evaluation.isLoading ? <div className="panel"><Skeleton /></div> : null}
    {report ? <section className="forecast-eval-metrics">
      <article className="panel"><span>预测覆盖率</span><strong>{percent(report.coverage)}</strong><small>{report.eligible_count}/{report.sample_count} 条已到期且可评估</small></article>
      <article className="panel"><span>方向命中率</span><strong>{percent(report.accuracy)}</strong><small>取最大概率类别与真实标签比较</small></article>
      <article className="panel"><span>Brier Score</span><strong>{decimal(report.brier_score)}</strong><small>越低越好，同时惩罚三类概率偏差</small></article>
      <article className="panel"><span>校准误差 ECE</span><strong>{decimal(report.expected_calibration_error)}</strong><small>预测置信度与真实命中率的差距</small></article>
    </section> : null}

    {report && report.sample_count === 0 ? <EmptyState>尚无固化预测。通过预测运行 API 生成记录，待预测周期到期后结算真实结果。</EmptyState> : null}

    {report && report.sample_count > 0 ? <section className="forecast-eval-grid">
      <article className="panel forecast-reliability"><div className="section-heading"><div><span className="eyebrow">Reliability</span><h2>概率可靠性</h2></div></div>
        {usefulBins.length === 0 ? <EmptyState>尚无已到期样本可绘制</EmptyState> : <div className="forecast-reliability__chart">{usefulBins.map((item) => <div className="forecast-reliability__bin" key={item.lower}><div className="forecast-reliability__bars"><i style={{ height: `${(item.mean_confidence || 0) * 100}%` }} /><b style={{ height: `${(item.empirical_accuracy || 0) * 100}%` }} /></div><span>{Math.round(item.lower * 100)}–{Math.round(item.upper * 100)}%</span><small>n={item.count}</small></div>)}</div>}
        <footer className="forecast-reliability__legend"><span><i />平均置信度</span><span><b />实际命中率</span></footer>
      </article>
      <article className="panel forecast-eval-breakdown"><div className="section-heading"><div><span className="eyebrow">Dataset</span><h2>样本构成</h2></div></div>
        <dl><div><dt>上涨标签</dt><dd>{report.class_counts.up}</dd></div><div><dt>震荡标签</dt><dd>{report.class_counts.flat}</dd></div><div><dt>下跌标签</dt><dd>{report.class_counts.down}</dd></div>{Object.entries(evaluation.data?.exclusions || {}).map(([key, value]) => <div key={key}><dt>{key === "outcome_not_observed" ? "尚未到期" : "预测时数据不足"}</dt><dd>{value}</dd></div>)}</dl>
      </article>
    </section> : null}

    <section className="panel forecast-calibrations"><div className="section-heading"><div><span className="eyebrow">Model registry</span><h2>校准版本</h2><p>只有通过样本门、审核并发布的版本才可进入在线预测。</p></div></div>
      {calibrations.isLoading ? <Skeleton /> : calibrations.data?.length ? <div className="table-scroll"><table><thead><tr><th>版本</th><th>方法</th><th>状态</th><th>训练区间</th><th>样本</th><th>参数</th></tr></thead><tbody>{calibrations.data.map((item) => <tr key={item.id}><td>{item.version}</td><td>{item.method}</td><td><span className={`status-chip ${item.status === "published" ? "ok" : "warn"}`}>{item.status}</span></td><td>{new Date(item.train_start).toLocaleDateString("zh-CN")} – {new Date(item.train_end).toLocaleDateString("zh-CN")}</td><td>{item.sample_count}</td><td>{item.parameters.temperature ? `T=${item.parameters.temperature}` : "—"}</td></tr>)}</tbody></table></div> : <EmptyState>当前市场与周期尚无校准版本</EmptyState>}
    </section>

    <section className="panel forecast-calibrations"><div className="section-heading"><div><span className="eyebrow">Champion / challenger</span><h2>模型对比决策</h2><p>仅使用不同版本在相同标的、时点和周期上都已结算的样本，避免样本选择偏差。</p></div>{comparison.data ? <StatusBadge value={comparison.data.decision} /> : null}</div>
      {comparison.isLoading ? <Skeleton /> : comparison.isError ? <ErrorState>模型对比加载失败</ErrorState> : comparison.data?.entries.length ? <><p className="muted">可比样本：{comparison.data.comparable_sample_count} · 建议：{comparison.data.recommended_model_key || "暂不建议切换"}</p><div className="table-scroll"><table><thead><tr><th>规则版本</th><th>Brier</th><th>Log Loss</th><th>ECE</th><th>命中率</th></tr></thead><tbody>{comparison.data.entries.map((item) => <tr key={item.model_key}><td className="mono">{item.model_key}</td><td>{decimal(item.report.brier_score)}</td><td>{decimal(item.report.log_loss)}</td><td>{decimal(item.report.expected_calibration_error)}</td><td>{percent(item.report.accuracy)}</td></tr>)}</tbody></table></div><small className="muted">{comparison.data.decision_reasons.join("；")}</small></> : <EmptyState>至少需要两个规则版本在相同预测槽位上拥有已结算样本，才能进行公平比较。</EmptyState>}
    </section>

    <details className="market-outlook-methodology"><summary>如何理解评估指标？</summary><div className="market-outlook-methodology__body"><p>命中率只看最终类别是否正确；Brier Score 和 Log Loss 同时检查概率分配，能够识别“方向碰巧正确但过度自信”的模型。可靠性图中两根柱越接近，说明预测概率越可信。</p><p className="market-outlook-methodology__note">训练采用按时间扩展的 walk-forward 切分，并在训练集与测试集之间加入 purge/embargo，防止相邻预测周期共享未来收益标签。</p></div></details>
  </>;
}
