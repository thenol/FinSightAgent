import { describe, expect, it } from "vitest";
import { buildMonthDays } from "./futureCalendarUtils";

describe("buildMonthDays", () => {
  it("always returns a complete six-week grid", () => {
    expect(buildMonthDays(new Date("2026-08-01T00:00:00Z"))).toHaveLength(42);
    expect(buildMonthDays(new Date("2026-02-01T00:00:00Z"))).toHaveLength(42);
  });

  it("starts on Monday and includes adjacent month dates", () => {
    const days = buildMonthDays(new Date("2026-06-01T00:00:00Z"));
    expect(days[0]?.toISOString().slice(0, 10)).toBe("2026-06-01");
    expect(days[41]?.toISOString().slice(0, 10)).toBe("2026-07-12");
  });
});
