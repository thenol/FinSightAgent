import { describe, expect, it } from "vitest";
import { buildGraphOption, directionColor } from "./ImpactAnalysisPanel";
import type { ImpactAnalysis } from "@/types/api";

const sampleAnalysis: ImpactAnalysis = {
  id: "imp_001",
  event_id: "evt_001",
  version: 1,
  status: "approved",
  event_title_snapshot: "美联储加息",
  summary: "加息压制成长股",
  transmission_chains: [
    {
      chain_id: "chn_rate",
      mechanism: "利率传导",
      steps: [
        { step: 0, description: "美联储加息" },
        { step: 1, description: "融资成本上升" },
      ],
      confidence: 0.7,
    },
  ],
  impacts: [
    {
      target_type: "sector",
      target_name: "银行",
      direction: "positive",
      magnitude: "moderate",
      horizon: "medium",
      confidence: 0.65,
      rationale: "息差扩大",
      chain_refs: ["chn_rate"],
    },
    {
      target_type: "sector",
      target_name: "地产",
      direction: "negative",
      magnitude: "strong",
      horizon: "medium",
      confidence: 0.7,
      rationale: "融资成本上升",
    },
  ],
  macro_assumptions: [],
  watch_items: [],
  generated_by: "agent",
  degraded: false,
};

describe("buildGraphOption", () => {
  it("creates event node and chain/impact nodes", () => {
    const option = buildGraphOption(sampleAnalysis);
    const series = (option.series as Array<Record<string, unknown>>)?.[0] as {
      data: Array<{ id: string; name: string }>;
      links: unknown[];
    };
    const ids = series.data.map((node) => node.id);
    expect(ids).toContain("event");
    expect(ids).toContain("impact-银行");
    expect(ids).toContain("impact-地产");
    expect(ids.some((id) => id.startsWith("chn_rate-step-"))).toBe(true);
    expect(series.links.length).toBeGreaterThan(0);
  });

  it("links impact back to chain end node when chain_ref matches", () => {
    const option = buildGraphOption(sampleAnalysis);
    const series = (option.series as Array<Record<string, unknown>>)?.[0] as {
      links: Array<{ source: string; target: string }>;
    };
    const bankLink = series.links.find((link) => link.target === "impact-银行");
    expect(bankLink).toBeDefined();
    expect(bankLink?.source).toMatch(/^chn_rate-step-/);
  });
});

describe("directionColor", () => {
  it("maps directions to expected colors", () => {
    expect(directionColor("positive")).toBe("#16a34a");
    expect(directionColor("negative")).toBe("#dc2626");
    expect(directionColor("neutral")).toBe("#6b7280");
    expect(directionColor("mixed")).toBe("#d97706");
  });
});
