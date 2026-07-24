import { describe, expect, it } from "vitest";
import { matchesEventScope, MVP_EVENT_TYPES, labelStatus } from "./format";

describe("event type labels and scope", () => {
  it("labels mvp and non-mvp types in Chinese", () => {
    expect(labelStatus("general_market_news")).toBe("综合资讯");
    expect(labelStatus("out_of_scope")).toBe("范围外");
    expect(labelStatus("earnings_guidance")).toBe("业绩预告");
  });

  it("filters research vs general scopes", () => {
    expect(matchesEventScope("earnings_guidance", "research")).toBe(true);
    expect(matchesEventScope("general_market_news", "research")).toBe(false);
    expect(matchesEventScope("general_market_news", "general")).toBe(true);
    expect(matchesEventScope("out_of_scope", "general")).toBe(false);
    expect(matchesEventScope("out_of_scope", "all")).toBe(true);
    expect(MVP_EVENT_TYPES.has("major_contract")).toBe(true);
  });
});
