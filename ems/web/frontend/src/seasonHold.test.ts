import { describe, expect, test } from "vitest";

import { seasonHoldActions, winterHoldLabel, type StrategyHysteresis } from "./seasonHold";

const holding: StrategyHysteresis = {
  committed: "winter",
  pending: "summer",
  count: 2,
  days: 3,
};

describe("winter hold chip (F-39 / #345)", () => {
  test("fixture 2/3 reads Winter vasthouden 2/3", () => {
    expect(winterHoldLabel(holding)).toBe("Winter vasthouden 2/3");
  });

  test("no chip when nothing is pending or another season is committed", () => {
    expect(winterHoldLabel({ ...holding, pending: null, count: 0 })).toBeNull();
    expect(winterHoldLabel({ ...holding, committed: "summer", pending: "winter" })).toBeNull();
    expect(winterHoldLabel(null)).toBeNull();
  });

  test("Nu solar-first only while auto is holding winter; Terug naar Auto when mode is summer", () => {
    expect(seasonHoldActions("auto", holding)).toEqual({
      holdLabel: "Winter vasthouden 2/3",
      forceSummer: true,
      backToAuto: false,
    });
    expect(seasonHoldActions("summer", holding)).toEqual({
      holdLabel: null,
      forceSummer: false,
      backToAuto: true,
    });
    expect(seasonHoldActions("auto", { ...holding, pending: null })).toEqual({
      holdLabel: null,
      forceSummer: false,
      backToAuto: false,
    });
  });
});
