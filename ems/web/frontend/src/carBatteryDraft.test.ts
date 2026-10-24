import { describe, expect, it } from "vitest";

import {
  carBatteryConsequence,
  carBatteryDraftDiff,
  carBatterySettingsBody,
  clampDischargeW,
  showAboveHouseWarning,
} from "./carBatteryDraft";

const saved = { mode: "hold" as const, dischargeW: 800 };

describe("car battery draft (#365)", () => {
  it("says the home battery holds and does not discharge", () => {
    expect(carBatteryConsequence({ mode: "hold", dischargeW: 800 })).toBe(
      "The home battery holds and does not discharge while the car charges.",
    );
  });

  it("includes the fixed wattage the home battery will discharge", () => {
    expect(carBatteryConsequence({ mode: "static_discharge", dischargeW: 800 })).toBe(
      "The home battery discharges at a fixed 800 W while the car charges.",
    );
  });

  it("says the home battery covers the house automatically", () => {
    expect(carBatteryConsequence({ mode: "match_home_load", dischargeW: 800 })).toBe(
      "The home battery covers the house automatically while the car charges.",
    );
  });

  it("treats an unchanged draft as no diff and no settings body", () => {
    const draft = { mode: "hold" as const, dischargeW: 800 };
    expect(carBatteryDraftDiff(saved, draft)).toEqual({ mode: false, dischargeW: false });
    expect(carBatterySettingsBody(saved, draft)).toBeNull();
  });

  it("sends mode and wattage together when only the mode changed", () => {
    const draft = { mode: "static_discharge" as const, dischargeW: 800 };
    expect(carBatteryDraftDiff(saved, draft)).toEqual({ mode: true, dischargeW: false });
    expect(carBatterySettingsBody(saved, draft)).toEqual({
      "control.car_charging_battery_mode": "static_discharge",
      "control.car_discharge_w": 800,
    });
  });

  it("sends mode and wattage together when only the wattage changed", () => {
    const holdSaved = { mode: "static_discharge" as const, dischargeW: 800 };
    const draft = { mode: "static_discharge" as const, dischargeW: 1200 };
    expect(carBatteryDraftDiff(holdSaved, draft)).toEqual({ mode: false, dischargeW: true });
    expect(carBatterySettingsBody(holdSaved, draft)).toEqual({
      "control.car_charging_battery_mode": "static_discharge",
      "control.car_discharge_w": 1200,
    });
  });

  it("sends the automatic mode together with the current wattage", () => {
    const draft = { mode: "match_home_load" as const, dischargeW: 800 };
    expect(carBatterySettingsBody(saved, draft)).toEqual({
      "control.car_charging_battery_mode": "match_home_load",
      "control.car_discharge_w": 800,
    });
  });

  it("compares wattage after clamp so a step-aligned edit is the saved value", () => {
    const staticSaved = { mode: "static_discharge" as const, dischargeW: 950 };
    expect(carBatteryDraftDiff(staticSaved, { mode: "static_discharge", dischargeW: 925 })).toEqual({
      mode: false,
      dischargeW: false,
    });
    expect(carBatterySettingsBody(staticSaved, { mode: "static_discharge", dischargeW: 925 })).toBeNull();
  });

  it("posts the clamped wattage when the entered value is off-step", () => {
    const staticSaved = { mode: "static_discharge" as const, dischargeW: 800 };
    expect(carBatterySettingsBody(staticSaved, { mode: "static_discharge", dischargeW: 925 })).toEqual({
      "control.car_charging_battery_mode": "static_discharge",
      "control.car_discharge_w": 950,
    });
  });

  it("clamps discharge watts to the 100–5000 W step of 50", () => {
    expect(clampDischargeW(925, 800)).toBe(950);
    expect(clampDischargeW(50, 800)).toBe(100);
    expect(clampDischargeW(9000, 800)).toBe(5000);
    expect(clampDischargeW(Number.NaN, 800)).toBe(800);
  });

  it("shows the above-house warning from the entered watts before any save", () => {
    expect(showAboveHouseWarning("static_discharge", 900, 600)).toBe(true);
    expect(showAboveHouseWarning("static_discharge", 500, 600)).toBe(false);
    expect(showAboveHouseWarning("static_discharge", 600, 600)).toBe(false);
    expect(showAboveHouseWarning("static_discharge", 900, null)).toBe(false);
    expect(showAboveHouseWarning("hold", 900, 600)).toBe(false);
    expect(showAboveHouseWarning("match_home_load", 900, 600)).toBe(false);
    expect(showAboveHouseWarning("static_discharge", Number.NaN, 600)).toBe(false);
  });
});
