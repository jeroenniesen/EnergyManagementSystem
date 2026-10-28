import { describe, expect, it } from "vitest";

import { tradingDryRunStepOk, tradingStepStatusText } from "./tradingSteps";

describe("tradingStepStatusText", () => {
  it("describes ok and missing steps for screen readers", () => {
    expect(
      tradingStepStatusText({
        id: "dry_run",
        ok: false,
        label: "Stap 2",
        missing: "niet vastgelegd",
      }),
    ).toContain("ontbreekt");
    expect(
      tradingStepStatusText({
        id: "advice",
        ok: true,
        label: "Stap 1",
        missing: null,
      }),
    ).toContain("voltooid");
  });
});

describe("tradingDryRunStepOk", () => {
  it("returns true only when the dry_run step is ok", () => {
    expect(
      tradingDryRunStepOk([
        { id: "advice", ok: true, label: "a", missing: null },
        { id: "dry_run", ok: false, label: "b", missing: "x" },
        { id: "arm", ok: false, label: "c", missing: null },
      ]),
    ).toBe(false);
    expect(
      tradingDryRunStepOk([
        { id: "dry_run", ok: true, label: "b", missing: null },
      ]),
    ).toBe(true);
  });
});
