import { describe, expect, it } from "vitest";
import {
  BUSINESS_ROLES,
  canManageDocuments,
  canReview,
  canViewAudit,
  hasBusinessRole,
} from "@/lib/roles";

describe("role helpers", () => {
  it("treats admin as a business role with elevated permissions", () => {
    expect(hasBusinessRole("admin")).toBe(true);
    expect(canReview("admin")).toBe(true);
    expect(canManageDocuments("admin")).toBe(true);
    expect(canViewAudit("admin")).toBe(true);
  });

  it("restricts reviewer and publisher capabilities", () => {
    expect(canReview("reviewer")).toBe(true);
    expect(canManageDocuments("reviewer")).toBe(false);
    expect(canViewAudit("publisher")).toBe(true);
    expect(hasBusinessRole("publisher")).toBe(true);
  });

  it("keeps the business role list aligned with backend BUSINESS_ROLES", () => {
    expect(BUSINESS_ROLES).toEqual(["researcher", "reviewer", "publisher", "admin"]);
  });
});
