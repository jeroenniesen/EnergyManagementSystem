import { describe, expect, it } from "vitest";

import {
  batteryActionLabel,
  formatBatteryActionWhy,
  isSlice1BatteryAction,
  type DecisionReason,
} from "./decisionWhy";

function reason(overrides: Partial<DecisionReason> = {}): DecisionReason {
  return {
    chosen_window: {
      start: "2026-01-15T02:00:00+00:00",
      end: "2026-01-15T04:00:00+00:00",
      intent: "grid_charge_to_target",
      label: "cheap charge window",
      eur_per_kwh_min: 0.08,
      eur_per_kwh_max: 0.12,
    },
    rejected_alternative: {
      intent: "allow_self_consumption",
      reason: "self-consumption only — rejected",
      window_start: null,
      window_end: null,
    },
    expected_benefit: { eur: 1.25, summary: "Estimated net benefit ≈ €1.25 for this plan." },
    risk: { margin_eur_per_kwh: 0.02, summary: "Risk margin €0.020/kWh." },
    safety_constraint: { code: null, message: null, action: "proceed" },
    gates: {
      validator_code: null,
      failsafe: false,
      dwell: false,
      cap_reached: false,
      unconfirmed: false,
    },
    summary: "Grid top-up is planned to reach the battery target.",
    ...overrides,
  };
}

describe("batteryActionLabel (zelfconsumptie vs volle snelheid)", () => {
  it("maps discharge_for_load action to zelfconsumptie, not ontladen", () => {
    expect(batteryActionLabel("discharge")).toBe("Zelfconsumptie");
    expect(batteryActionLabel("self_consume")).toBe("Zelfconsumptie");
    expect(batteryActionLabel("self_consumption")).toBe("Zelfconsumptie");
    expect(batteryActionLabel("discharge").toLowerCase()).not.toMatch(/ontladen/);
  });

  it("maps forced dump to op volle snelheid ontladen", () => {
    expect(batteryActionLabel("full_speed_discharge")).toBe("Op volle snelheid ontladen");
  });

  it("keeps charge / hold labels", () => {
    expect(batteryActionLabel("grid_charge")).toBe("Laden van het net");
    expect(batteryActionLabel("hold")).toBe("Vasthouden");
  });
});

describe("isSlice1BatteryAction", () => {
  it("accepts laden / vasthouden / zelfconsumptie / volle-snelheid", () => {
    expect(isSlice1BatteryAction("grid_charge")).toBe(true);
    expect(isSlice1BatteryAction("hold")).toBe(true);
    expect(isSlice1BatteryAction("discharge")).toBe(true);
    expect(isSlice1BatteryAction("full_speed_discharge")).toBe(true);
    expect(isSlice1BatteryAction("paused")).toBe(false);
    expect(isSlice1BatteryAction("self_consume")).toBe(false);
    expect(isSlice1BatteryAction("solar_charge")).toBe(false);
  });
});

describe("formatBatteryActionWhy (#85 slice 1)", () => {
  it("returns null for out-of-scope actions and missing reason", () => {
    expect(formatBatteryActionWhy(reason(), "paused", { dryRun: true })).toBeNull();
    expect(formatBatteryActionWhy(null, "grid_charge", { dryRun: true })).toBeNull();
  });

  it("explains grid charge with expected euro benefit from the reason object", () => {
    const text = formatBatteryActionWhy(reason(), "grid_charge", { dryRun: false });
    expect(text).toMatch(/van het net/i);
    expect(text).toContain("€1.25");
    expect(text).toMatch(/voordeel/i);
    // Benefit figure comes from reason.expected_benefit.eur, not a hardcoded constant.
    const other = formatBatteryActionWhy(
      reason({ expected_benefit: { eur: 0.42, summary: "other" } }),
      "grid_charge",
      { dryRun: false },
    );
    expect(other).toContain("€0.42");
    expect(other).not.toContain("€1.25");
  });

  it("explains hold and discharge(=zelfconsumptie) without calling it ontladen", () => {
    const hold = formatBatteryActionWhy(
      reason({
        chosen_window: {
          start: null,
          end: null,
          intent: "hold_reserve",
          label: "hold-reserve window",
          eur_per_kwh_min: null,
          eur_per_kwh_max: null,
        },
      }),
      "hold",
      { dryRun: false },
    );
    expect(hold).toMatch(/vasthouden|vast/i);
    expect(hold!.split(/(?<=[.!?])\s+/).length).toBeLessThanOrEqual(2);

    const discharge = formatBatteryActionWhy(
      reason({
        chosen_window: {
          start: null,
          end: null,
          intent: "discharge_for_load",
          label: "expensive self-consumption window",
          eur_per_kwh_min: 0.4,
          eur_per_kwh_max: 0.5,
        },
      }),
      "discharge",
      { dryRun: false },
    );
    expect(discharge).toMatch(/zelfconsumptie/i);
    expect(discharge!.toLowerCase()).not.toMatch(/ontlaadt|ontladen/);
    expect(discharge).toContain("€1.25");
  });

  it("explains full-speed discharge as op volle snelheid ontladen", () => {
    const text = formatBatteryActionWhy(reason(), "full_speed_discharge", { dryRun: false });
    expect(text).toMatch(/volle snelheid/i);
    expect(text).toMatch(/ontlaadt/i);
  });

  it("in dry-run uses 'zou' and never 'doet'", () => {
    for (const action of ["grid_charge", "hold", "discharge", "full_speed_discharge"] as const) {
      const text = formatBatteryActionWhy(reason(), action, { dryRun: true });
      expect(text, action).toMatch(/\bzou\b/);
      expect(text!.toLowerCase(), action).not.toMatch(/\bdoet\b/);
    }
  });

  it("says honestly that it is about safety when there is no positive euro benefit", () => {
    const text = formatBatteryActionWhy(
      reason({
        expected_benefit: { eur: 0, summary: "No positive arbitrage benefit." },
      }),
      "hold",
      { dryRun: true },
    );
    expect(text).toMatch(/veiligheid/i);
    expect(text).not.toMatch(/voordeel ≈/i);
  });

  it("prefers safety wording when the reason safety_constraint is paused or failsafe", () => {
    const paused = formatBatteryActionWhy(
      reason({
        safety_constraint: { code: "stale_inputs", message: "stale", action: "paused" },
        expected_benefit: { eur: 2, summary: "would save" },
      }),
      "grid_charge",
      { dryRun: true },
    );
    expect(paused).toMatch(/veiligheid/i);

    const failsafe = formatBatteryActionWhy(
      reason({
        gates: {
          validator_code: "stale_inputs",
          failsafe: true,
          dwell: false,
          cap_reached: false,
          unconfirmed: false,
        },
      }),
      "discharge",
      { dryRun: false },
    );
    expect(failsafe).toMatch(/veiligheid/i);
    expect(failsafe).toMatch(/zelfconsumptie/i);
  });

  it("uses chosen_window price from the reason object when charging", () => {
    const text = formatBatteryActionWhy(reason(), "grid_charge", { dryRun: true });
    expect(text).toContain("€0.08");
  });
});
