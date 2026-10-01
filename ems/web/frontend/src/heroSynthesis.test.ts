import { describe, expect, test } from "vitest";

import { buildHeroSynthesis, isPlannerJargon } from "./heroSynthesis";

describe("isPlannerJargon", () => {
  test("flags break-even and raw self-consumption operator forms", () => {
    expect(
      isPlannerJargon("self-consumption: €0.45/kWh > break-even €0.32"),
    ).toBe(true);
    expect(isPlannerJargon("no-trade: spread below break-even")).toBe(true);
    expect(isPlannerJargon("discharge: €0.40/kWh > break-even €0.30")).toBe(true);
  });

  test("flags EV-load operator prose", () => {
    expect(isPlannerJargon("EV load expected ~12 kWh (manual day override)")).toBe(
      true,
    );
  });

  test("allows plain homeowner sentences", () => {
    expect(isPlannerJargon("Battery is following the current plan.")).toBe(false);
    expect(isPlannerJargon("On track for tonight's target.")).toBe(false);
    expect(isPlannerJargon("A brilliant day for clean energy")).toBe(false);
    expect(
      isPlannerJargon("Short of the 88% target with no grid top-up planned."),
    ).toBe(false);
  });
});

describe("buildHeroSynthesis", () => {
  test("prefers the on-track story message when present", () => {
    expect(
      buildHeroSynthesis({
        currentReason: "Battery is following the current plan.",
        onTrackMessage: "On track for tonight's target.",
        scoreSummary: "A brilliant day for clean energy",
      }),
    ).toBe("On track for tonight's target");
  });

  test("skips jargon current_reason and falls through to score summary", () => {
    expect(
      buildHeroSynthesis({
        currentReason: "self-consumption: €0.45/kWh > break-even €0.32",
        onTrackMessage: null,
        scoreSummary: "A solid energy day — keep it up",
      }),
    ).toBe("A solid energy day — keep it up");
  });

  test("uses a plain current_reason when no on-track message", () => {
    expect(
      buildHeroSynthesis({
        currentReason: "Battery is following the current plan.",
        onTrackMessage: null,
        scoreSummary: null,
      }),
    ).toBe("Battery is following the current plan");
  });

  test("never joins middot fragments — one sentence only", () => {
    const text = buildHeroSynthesis({
      currentReason: "Battery is following the current plan.",
      onTrackMessage: "On track.",
      scoreSummary: "A brilliant day for clean energy",
    });
    expect(text).not.toContain("·");
    expect(text).toBe("On track");
  });

  test("calm fallback when every candidate is jargon or missing", () => {
    expect(
      buildHeroSynthesis({
        currentReason: "EV load expected ~8 kWh (EV day hint)",
        onTrackMessage: null,
        scoreSummary: null,
      }),
    ).toBe("Battery is following the current plan.");
  });
});
