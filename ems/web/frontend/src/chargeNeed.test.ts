import { describe, expect, test } from "vitest";

import { isUsableChargeNeed } from "./App";

describe("isUsableChargeNeed (#134 / PR #146 B2)", () => {
  test("accepts a complete charge-need payload", () => {
    expect(
      isUsableChargeNeed({
        current_soc_pct: 55,
        target_soc_pct: 80,
        deficit_kwh: 2.5,
        on_track: false,
        reason: "Need more charge.",
      }),
    ).toBe(true);
  });

  test("rejects null-float unknown payloads that would crash toFixed", () => {
    expect(
      isUsableChargeNeed({
        current_soc_pct: null,
        target_soc_pct: null,
        deficit_kwh: null,
        on_track: null,
        reason: "Battery level unknown — waiting for a fresh reading.",
      }),
    ).toBe(false);
  });

  test("rejects empty / missing bodies (204 → cleared card)", () => {
    expect(isUsableChargeNeed(null)).toBe(false);
    expect(isUsableChargeNeed(undefined)).toBe(false);
    expect(isUsableChargeNeed({})).toBe(false);
  });
});
