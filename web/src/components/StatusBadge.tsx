import { labelStatus, statusTone } from "@/lib/format";

export function StatusBadge({ value }: { value: string }) {
  return <span className={`status-badge ${statusTone(value)} status-${value}`}>{labelStatus(value)}</span>;
}
