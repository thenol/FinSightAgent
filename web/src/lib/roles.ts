import type { Role } from "@/types/api";

export function canManageSources(role: Role | null): boolean {
  return role === "admin";
}

export function canManageDocuments(role: Role | null): boolean {
  return role === "admin";
}

export function canManageLlm(role: Role | null): boolean {
  return role === "admin";
}

export function canReview(role: Role | null): boolean {
  return role === "reviewer" || role === "admin";
}

export function canRunWorkflow(role: Role | null): boolean {
  return role === "researcher" || role === "reviewer" || role === "admin";
}

export function canRunResearch(role: Role | null): boolean {
  return role === "researcher" || role === "reviewer" || role === "admin";
}

export function canPublish(role: Role | null): boolean {
  return role === "publisher" || role === "admin";
}

export function allowedReportTransitions(
  status: string,
  role: Role | null,
): string[] {
  const map: Record<string, string[]> = {
    needs_review: ["approved"],
    review_required: ["approved"],
    approved: ["published", "needs_revision"],
    published: ["withdrawn"],
    needs_revision: ["approved"],
  };
  const next = map[status] || [];
  return next.filter((target) => {
    if (target === "approved" || target === "needs_revision") return canReview(role);
    if (target === "published" || target === "withdrawn") return canPublish(role);
    return false;
  });
}
