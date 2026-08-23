import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { apiGet } from "@/lib/api";
import type { FutureCalendarDay, FutureCalendarSummary, FutureEventDetail } from "@/types/api";
import { buildMonthDays } from "./futureCalendarUtils";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"] as const;
type FocusMode = "all" | "major" | "conflict";

function isoDay(date: Date): string { return date.toISOString().slice(0, 10); }
function monthStart(value: string): Date { const date = new Date(`${value}-01T00:00:00Z`); return Number.isNaN(date.getTime()) ? new Date() : date; }
function monthKey(date: Date): string { return date.toISOString().slice(0, 7); }
function monthEnd(date: Date): Date { return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0, 23, 59, 59)); }
function formatTime(value?: string | null): string {
  if (!value) return "时间待确认";
  return new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Shanghai" });
}

export function FutureEventsPage() {
  const [params, setParams] = useSearchParams();
  const initialMonth = params.get("month") || monthKey(new Date());
  const [month, setMonth] = useState(initialMonth);
  const [selectedDate, setSelectedDate] = useState(params.get("date") || `${initialMonth}-01`);
  const [focus, setFocus] = useState<FocusMode>((params.get("focus") as FocusMode) || "all");
  const [eventId, setEventId] = useState<string | null>(null);
  const targetId = params.get("target_id") || undefined;
  const start = monthStart(month);
  const end = monthEnd(start);
  const summaryQuery = useQuery({
    queryKey: ["future-calendar-summary", month, targetId],
    queryFn: () => apiGet<FutureCalendarSummary[]>(`/api/v1/future-calendar/summary?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}${targetId ? `&target_id=${encodeURIComponent(targetId)}` : ""}`),
  });
  const dayQuery = useQuery({
    queryKey: ["future-calendar-day", selectedDate, targetId],
    queryFn: () => apiGet<FutureCalendarDay>(`/api/v1/future-calendar/day?date=${selectedDate}${targetId ? `&target_id=${encodeURIComponent(targetId)}` : ""}`),
  });
  const summary = new Map((summaryQuery.data || []).map((item) => [item.date, item]));
  const days = useMemo(() => buildMonthDays(start), [start]);
  const totals = useMemo(() => (summaryQuery.data || []).reduce((result, item) => ({
    events: result.events + item.event_count,
    major: result.major + item.major_event_count,
    positive: result.positive + (item.direction === "positive" ? 1 : 0),
    negative: result.negative + (item.direction === "negative" ? 1 : 0),
    conflicts: result.conflicts + (item.has_conflict ? 1 : 0),
  }), { events: 0, major: 0, positive: 0, negative: 0, conflicts: 0 }), [summaryQuery.data]);

  function updateParams(next: Record<string, string>) {
    setParams((current) => { Object.entries(next).forEach(([key, value]) => current.set(key, value)); return current; });
  }
  function moveMonth(delta: number) {
    const next = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + delta, 1));
    const value = monthKey(next);
    setMonth(value);
    setSelectedDate(`${value}-01`);
    updateParams({ month: value, date: `${value}-01` });
  }
  function selectDay(value: string) {
    setSelectedDate(value);
    if (value.slice(0, 7) !== month) setMonth(value.slice(0, 7));
    updateParams({ month: value.slice(0, 7), date: value });
  }
  function setFocusMode(value: FocusMode) {
    setFocus(value);
    updateParams({ focus: value });
  }
  function goToday() {
    const value = isoDay(new Date());
    setMonth(value.slice(0, 7));
    setSelectedDate(value);
    updateParams({ month: value.slice(0, 7), date: value });
  }
  const selectedSummary = summary.get(selectedDate);

  return (
    <>
      <PageHeader eyebrow="Future event calendar" title="研究日历" description={targetId ? "当前已按目标筛选；从日历定位事件，并在右侧查看影响依据。" : "用事件时间、重要度和影响方向组织未来研究工作。"} />
      <section className="future-calendar-toolbar" aria-label="日历导航">
        <div className="future-calendar-toolbar__nav">
          <button type="button" className="button ghost sm" onClick={() => moveMonth(-1)} aria-label="上个月">‹</button>
          <input aria-label="选择月份" type="month" value={month} onChange={(event) => { setMonth(event.target.value); setSelectedDate(`${event.target.value}-01`); updateParams({ month: event.target.value, date: `${event.target.value}-01` }); }} />
          <button type="button" className="button ghost sm" onClick={() => moveMonth(1)} aria-label="下个月">›</button>
          <button type="button" className="button ghost sm" onClick={goToday}>今天</button>
        </div>
        <div className="future-calendar-toolbar__title">{start.toLocaleDateString("zh-CN", { year: "numeric", month: "long", timeZone: "UTC" })}</div>
        <div className="future-calendar-toolbar__actions">
          <div className="future-calendar-segmented" aria-label="日历聚焦模式">
            {(["all", "major", "conflict"] as const).map((mode) => <button type="button" key={mode} className={focus === mode ? "is-active" : ""} onClick={() => setFocusMode(mode)}>{mode === "all" ? "全部" : mode === "major" ? "重大" : "冲突"}</button>)}
          </div>
          {targetId ? <Link className="button ghost sm" to={`/impact-targets/${encodeURIComponent(targetId)}`}>返回目标影响</Link> : null}
        </div>
      </section>
      <section className="future-calendar-insights" aria-label="月度研究摘要">
        {[{ label: "本月事件", value: totals.events }, { label: "重大事件", value: totals.major }, { label: "正向影响日", value: totals.positive }, { label: "负向影响日", value: totals.negative }, { label: "多空冲突日", value: totals.conflicts }].map((item) => <div className="future-calendar-insight" key={item.label}><span>{item.label}</span><strong>{item.value}</strong></div>)}
      </section>
      {summaryQuery.isLoading ? <div className="future-calendar-loading"><Skeleton /></div> : null}
      {summaryQuery.isError ? <ErrorState>未来事件摘要加载失败</ErrorState> : null}
      <div className="future-calendar-layout">
        <section className="panel future-calendar-grid" aria-label="月历">
          <div className="future-calendar-weekdays">{WEEKDAYS.map((label) => <div className="future-calendar-weekday" key={label}>{label}</div>)}</div>
          <div className="future-calendar-days">
            {days.map((day) => {
              if (!day) return <div className="future-calendar-cell is-empty" key="empty" />;
              const value = isoDay(day);
              const item = summary.get(value);
              const inMonth = value.slice(0, 7) === month;
              const isToday = value === isoDay(new Date());
              const isFocused = focus === "all" || (focus === "major" && Boolean(item?.major_event_count)) || (focus === "conflict" && Boolean(item?.has_conflict));
              return <button type="button" className={`future-calendar-cell ${inMonth ? "" : "is-adjacent"} ${value === selectedDate ? "is-selected" : ""} ${isToday ? "is-today" : ""} ${!isFocused ? "is-dimmed" : ""}`} key={value} onClick={() => selectDay(value)} aria-label={`${value}${item?.event_count ? `，${item.event_count} 个事件` : "，无事件"}`}>
                <div className="future-calendar-cell__head"><strong>{day.getUTCDate()}</strong>{isToday ? <span className="future-calendar-today">今</span> : null}</div>
                {item?.event_count ? <div className="future-calendar-cell__meta"><span>{item.event_count} 事件</span>{item.has_conflict ? <span className="future-calendar-conflict">冲突</span> : null}</div> : null}
                <div className="future-calendar-preview">{(item?.event_previews || []).slice(0, 2).map((event) => <span className={`future-calendar-event-pill is-${event.direction}`} key={`${value}-${event.id}`} title={event.title} role="button" tabIndex={0} onClick={(click) => { click.stopPropagation(); setEventId(event.id); }} onKeyDown={(keyboard) => { if (keyboard.key === "Enter" || keyboard.key === " ") { keyboard.preventDefault(); keyboard.stopPropagation(); setEventId(event.id); } }}><i />{event.title}</span>)}</div>
                {item?.hidden_event_count ? <small className="future-calendar-more">+{item.hidden_event_count} 更多</small> : null}
                {item?.event_count ? <div className={`future-calendar-strength is-${item.direction}`} style={{ opacity: Math.min(1, 0.25 + Math.abs(item.net_strength)) }} /> : null}
              </button>;
            })}
          </div>
          <div className="future-calendar-legend"><span><i className="is-positive" />利好</span><span><i className="is-negative" />利空</span><span><i className="is-mixed" />多空冲突</span><span><i className="is-neutral" />方向待确认</span></div>
        </section>
        <DayPanel query={dayQuery} selectedDate={selectedDate} selectedSummary={selectedSummary} onOpenEvent={setEventId} />
      </div>
      {eventId ? <EventDetailDrawer eventId={eventId} onClose={() => setEventId(null)} /> : null}
    </>
  );
}

function DayPanel({ query, selectedDate, selectedSummary, onOpenEvent }: { query: ReturnType<typeof useQuery<FutureCalendarDay>>; selectedDate: string; selectedSummary?: FutureCalendarSummary; onOpenEvent: (id: string) => void }) {
  if (query.isLoading) return <section className="panel future-day-panel"><Skeleton /></section>;
  if (query.isError || !query.data) return <section className="panel future-day-panel"><ErrorState>日期研究视图加载失败</ErrorState></section>;
  const day = query.data;
  return <section className="panel future-day-panel">
    <div className="future-day-panel__header"><div><span className="eyebrow">Selected date</span><h2>{new Date(`${selectedDate}T00:00:00Z`).toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long", timeZone: "UTC" })}</h2><p className="muted">{day.timezone} · {selectedSummary?.direction === "uncertain" ? "方向待确认" : `净影响：${selectedSummary?.direction || "无事件"}`}</p></div>{selectedSummary?.has_conflict ? <span className="future-conflict-badge">多空冲突</span> : null}</div>
    <section className="future-day-section"><div className="future-section-title"><h3>事件</h3><span>{day.scheduled_events.length}</span></div>{!day.scheduled_events.length ? <EmptyState>该日期暂无已批准未来事件</EmptyState> : <div className="future-event-list">{day.scheduled_events.map((event) => <button type="button" className="future-event-item" key={event.id} onClick={() => onOpenEvent(event.id)}><div className="future-event-item__title"><strong>{event.title}</strong><StatusBadge value={event.status} /></div><div className="muted">{event.target_name || "未指定目标"} · {event.event_type}</div><small>{formatTime(event.scheduled_from)} · 重要度 {Number(event.importance || 0).toFixed(2)}</small></button>)}</div>}</section>
    <section className="future-day-section"><div className="future-section-title"><h3>有效影响</h3><span>{day.active_impacts.length}</span></div>{!day.active_impacts.length ? <EmptyState>该日期暂无有效影响</EmptyState> : <div className="future-impact-list">{day.active_impacts.map((item) => <article className="future-impact-item" key={`${item.catalyst_id}-${item.target_id}`}><div><strong>{item.target_name}</strong><small>{item.event_title}</small></div><StatusBadge value={item.direction} /><small className="future-impact-rationale">条件强度 {Number(item.conditional_strength || 0).toFixed(2)}{item.occurrence_probability != null ? ` · 概率 ${(item.occurrence_probability * 100).toFixed(0)}%` : ""}{item.rationale ? ` · ${item.rationale}` : ""}</small></article>)}</div>}</section>
  </section>;
}

function EventDetailDrawer({ eventId, onClose }: { eventId: string; onClose: () => void }) {
  const query = useQuery({ queryKey: ["future-event", eventId], queryFn: () => apiGet<FutureEventDetail>(`/api/v1/future-events/${encodeURIComponent(eventId)}`) });
  const revision = query.data?.current_revision;
  const impact = query.data?.target_impacts?.[0];
  return <div className="future-event-drawer-backdrop" role="presentation" onClick={onClose}><aside className="future-event-drawer" role="dialog" aria-modal="true" aria-label="事件详情" onClick={(event) => event.stopPropagation()}><div className="future-event-drawer__header"><div><span className="eyebrow">Event detail</span><h2>{revision?.title || "事件详情"}</h2></div><button type="button" className="icon-button" onClick={onClose} aria-label="关闭">×</button></div>{query.isLoading ? <Skeleton /> : query.isError || !query.data || !revision ? <ErrorState>事件详情加载失败</ErrorState> : <><div className="future-event-detail-grid"><span>状态<strong><StatusBadge value={revision.status} /></strong></span><span>重要度<strong>{Number(revision.importance || 0).toFixed(2)}</strong></span><span>影响方向<strong><StatusBadge value={impact?.direction || "uncertain"} /></strong></span><span>发生时间<strong>{formatTime(revision.scheduled_from)}</strong></span></div><p className="muted">{impact?.target_id || "未指定目标"} · {query.data.event.event_type} · {query.data.event.kind}</p>{impact?.rationale ? <div className="future-event-rationale"><strong>影响依据</strong><p>{impact.rationale}</p></div> : revision.description ? <div className="future-event-rationale"><strong>事件说明</strong><p>{revision.description}</p></div> : null}</>}</aside></div>;
}
