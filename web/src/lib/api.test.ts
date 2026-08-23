import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { apiRequest, ApiError } from "@/lib/api";

describe("apiRequest", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns envelope data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ data: { ok: true }, meta: {} }),
      }),
    );
    await expect(apiRequest<{ ok: boolean }>("/api/v1/health")).resolves.toEqual({ ok: true });
  });

  it("maps 401 to AUTH_REQUIRED", async () => {
    sessionStorage.setItem("finsight.token", "token");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ error: { code: "AUTH_REQUIRED" } }),
      }),
    );
    await expect(apiRequest("/api/v1/reviews")).rejects.toBeInstanceOf(ApiError);
    expect(sessionStorage.getItem("finsight.token")).toBeNull();
  });

  it("notifies the auth provider when a token expires", async () => {
    const expired = vi.fn();
    window.addEventListener("finsight:auth-expired", expired);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ error: { code: "AUTH_TOKEN_INVALID" } }),
    }));
    await expect(apiRequest("/api/v1/future-calendar/summary")).rejects.toMatchObject({ code: "AUTH_REQUIRED" });
    expect(expired).toHaveBeenCalledTimes(1);
    window.removeEventListener("finsight:auth-expired", expired);
  });
});
