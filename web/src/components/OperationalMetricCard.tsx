import { Link } from "react-router-dom";

export type OperationalMetricTone = "critical" | "warning" | "attention" | "healthy" | "neutral";

export function OperationalMetricCard({ label, value, secondary, progress, tone = "neutral", href, icon }: { label: string; value: string | number; secondary: string; progress?: number; tone?: OperationalMetricTone; href?: string; icon: string }) {
  const body = <><header><span className="operational-metric__icon" aria-hidden="true">{icon}</span><span>{label}</span></header><strong>{value}</strong><p>{secondary}</p>{progress != null ? <span className="operational-metric__progress" aria-label={`${label} ${Math.round(progress * 100)}%`}><i style={{ width: `${Math.max(0, Math.min(100, progress * 100))}%` }} /></span> : null}</>;
  return href ? <Link className={`operational-metric is-${tone}`} to={href} aria-label={`${label}：${value}，${secondary}`}>{body}</Link> : <article className={`operational-metric is-${tone}`}>{body}</article>;
}
