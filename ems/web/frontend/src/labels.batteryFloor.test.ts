import { describe, expect, it } from "vitest";

import { HOME_BATTERY_FLOOR, isBelowReserveSoc } from "./labels";

describe("HOME_BATTERY_FLOOR (#310)", () => {
  it("formats reserve-above-device with jouw reserve", () => {
    expect(HOME_BATTERY_FLOOR.line(10, 10, 5)).toBe(
      "Vloer 10% (jouw reserve) · apparaat ≥ 5%",
    );
  });

  it("formats device-limited floor without jouw reserve", () => {
    expect(HOME_BATTERY_FLOOR.line(6, 5, 6)).toBe("Vloer 6% · apparaat ≥ 6%");
  });

  it("detects SoC below user reserve setting", () => {
    expect(isBelowReserveSoc(8, 10)).toBe(true);
    expect(isBelowReserveSoc(10, 10)).toBe(false);
    expect(isBelowReserveSoc(null, 10)).toBe(false);
  });
});
