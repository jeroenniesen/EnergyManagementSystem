import { describe, expect, it } from "vitest";
import {
  formatPeakCoveragePct,
  type EveningPeakCoverage,
} from "./EveningPeakCoverage";

describe("formatPeakCoveragePct", () => {
  it("formats a probability as a percent", () => {
    expect(formatPeakCoveragePct(0.666)).toBe("67%");
    expect(formatPeakCoveragePct(1)).toBe("100%");
    expect(formatPeakCoveragePct(0)).toBe("0%");
  });

  it("returns null when unavailable", () => {
    expect(formatPeakCoveragePct(null)).toBeNull();
    expect(formatPeakCoveragePct(undefined)).toBeNull();
    expect(formatPeakCoveragePct(Number.NaN)).toBeNull();
  });
});

describe("EveningPeakCoverage contract", () => {
  it("accepts the API shape used by /api/battery-plan", () => {
    const sample: EveningPeakCoverage = {
      probability: 0.667,
      available: true,
      label: "Evening peak may be covered (~67%)",
      reason: "2 of 3 scenarios cover the evening peak.",
      calibrated: true,
      scenarios_covering: 2,
      scenarios_total: 3,
      peak_kwh_expected: 3.2,
      available_kwh: 4.1,
    };
    expect(formatPeakCoveragePct(sample.probability)).toBe("67%");
  });
});
