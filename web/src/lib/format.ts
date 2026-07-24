export function formatDate(value?: string | null): string {
  if (!value) return "–";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function taskAge(value?: string | null): string {
  if (!value) return "–";
  const created = new Date(value).getTime();
  if (Number.isNaN(created)) return "–";
  const hours = Math.max(0, Math.floor((Date.now() - created) / 3_600_000));
  if (hours < 1) return "<1 小时";
  if (hours < 24) return `${hours} 小时`;
  const days = Math.floor(hours / 24);
  return `${days} 天`;
}

export function slaClass(value?: string | null): "ok" | "warn" | "bad" {
  if (!value) return "ok";
  const created = new Date(value).getTime();
  if (Number.isNaN(created)) return "ok";
  const hours = (Date.now() - created) / 3_600_000;
  if (hours >= 72) return "bad";
  if (hours >= 24) return "warn";
  return "ok";
}

export function statusTone(status: string): "ok" | "warn" | "bad" {
  if (["active", "succeeded", "published", "verified", "approved"].includes(status)) {
    return "ok";
  }
  if (["failed", "withdrawn", "rejected", "conflicted", "disabled"].includes(status)) {
    return "bad";
  }
  return "warn";
}

export const decisionNames: Record<string, string> = {
  approve: "批准",
  return: "退回",
  return_for_supplement: "退回补充",
  downgrade_to_fact_card: "降级为事实卡片",
  reject: "拒绝",
};

export const transitionNames: Record<string, string> = {
  approved: "批准",
  published: "发布",
  withdrawn: "撤回",
  needs_revision: "要求修订",
  needs_review: "提交审核",
};

export const statusNames: Record<string, string> = {
  active: "正常",
  disabled: "已禁用",
  degraded: "降级",
  pending: "待处理",
  decided: "已决定",
  running: "运行中",
  waiting_review: "等待审核",
  failed: "失败",
  succeeded: "成功",
  verified: "已验证",
  conflicted: "有冲突",
  unverified: "未验证",
  needs_review: "待审核",
  review_required: "要求审核",
  approved: "已批准",
  published: "已发布",
  withdrawn: "已撤回",
  needs_revision: "待修订",
  rejected: "已拒绝",
  triaged: "已分诊",
  dormant: "休眠",
  archived: "已归档",
  earnings_guidance: "业绩预告",
  major_contract: "重大合同",
  merger_acquisition: "并购重组",
  shareholder_reduction: "股东减持",
  regulatory_penalty: "监管处罚",
  general_market_news: "综合资讯",
  out_of_scope: "范围外",
  unsupported: "范围外（旧）",
};

export const MVP_EVENT_TYPES = new Set([
  "earnings_guidance",
  "major_contract",
  "merger_acquisition",
  "shareholder_reduction",
  "regulatory_penalty",
]);

export type EventListScope = "research" | "general" | "all";

export function matchesEventScope(eventType: string, scope: EventListScope): boolean {
  if (scope === "all") return true;
  if (scope === "research") return MVP_EVENT_TYPES.has(eventType);
  return eventType === "general_market_news";
}

export function labelStatus(value: string): string {
  return statusNames[value] || value || "–";
}

export function asList<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === "object" && Array.isArray((value as { items?: unknown }).items)) {
    return (value as { items: T[] }).items;
  }
  return [];
}
