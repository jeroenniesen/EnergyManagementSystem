import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BatteryActionWhy } from "./BatteryActionWhy";
import { POWER_EXCEEDS_WHY, type DecisionReason } from "./decisionWhy";

const REASON: DecisionReason = {
  chosen_window: {
    start: "2026-01-15T02:00:00+00:00",
    end: "2026-01-15T04:00:00+00:00",
    intent: "grid_charge_to_target",
    label: "cheap charge window",
    eur_per_kwh_min: 0.1,
    eur_per_kwh_max: 0.12,
  },
  rejected_alternative: null,
  expected_benefit: { eur: 0.85, summary: "Estimated net benefit ≈ €0.85." },
  risk: { margin_eur_per_kwh: 0.02, summary: "ok" },
  safety_constraint: { code: null, message: null, action: "proceed" },
  gates: {
    validator_code: null,
    failsafe: false,
    dwell: false,
    cap_reached: false,
    unconfirmed: false,
  },
  summary: "Grid top-up planned.",
};

describe("BatteryActionWhy", () => {
  it("shows Dutch waarom for paused states (#85 slice 2)", () => {
    const html = renderToStaticMarkup(
      createElement(BatteryActionWhy, {
        currentAction: "paused",
        reason: {
          ...REASON,
          safety_constraint: {
            code: "stale_inputs",
            message: "stale",
            action: "paused",
          },
        },
        dryRun: true,
      }),
    );
    expect(html).toContain('data-testid="battery-action-why"');
    expect(html).toContain('data-testid="decision-reason-details"');
    expect(html).toContain('data-testid="battery-action-why-text"');
    expect(html).toMatch(/zou pauzeren/i);
  });

  it("shows literal power-exceedance pause copy", () => {
    const html = renderToStaticMarkup(
      createElement(BatteryActionWhy, {
        currentAction: "paused",
        reason: {
          ...REASON,
          safety_constraint: {
            code: "power_exceeds_capability",
            message: "too much",
            action: "paused",
          },
        },
        dryRun: false,
      }),
    );
    expect(html).toContain(POWER_EXCEEDS_WHY);
  });

  it("hides the explanation text until the details disclosure is opened (SSR closed)", () => {
    const html = renderToStaticMarkup(
      createElement(BatteryActionWhy, {
        currentAction: "grid_charge",
        reason: REASON,
        dryRun: true,
      }),
    );
    expect(html).toContain('data-testid="battery-action-why"');
    expect(html).toContain("Waarom?");
    expect(html).toContain("Laden van het net");
    // Closed <details> still contains the paragraph in markup, but the summary is the only
    // always-visible control — e2e asserts the open-state interaction.
    expect(html).toContain('data-testid="battery-action-why-text"');
    expect(html).toContain('data-testid="decision-reason-details"');
    expect(html).toMatch(/zou.*laden/i);
    expect(html).toContain("€0.85");
    expect(html.toLowerCase()).not.toMatch(/\bdoet\b/);
  });

  it("marks dry-run on the section for tests and live wording when not dry-run", () => {
    const dry = renderToStaticMarkup(
      createElement(BatteryActionWhy, {
        currentAction: "hold",
        reason: REASON,
        dryRun: true,
      }),
    );
    expect(dry).toContain('data-dry-run="true"');
    expect(dry).toMatch(/zou/);

    const live = renderToStaticMarkup(
      createElement(BatteryActionWhy, {
        currentAction: "discharge",
        reason: REASON,
        dryRun: false,
      }),
    );
    expect(live).toContain('data-dry-run="false"');
    expect(live).toMatch(/zelfconsumptie/i);
    expect(live.toLowerCase()).not.toMatch(/ontlaadt|ontladen/);
    expect(live).not.toMatch(/\bzou\b/);
  });

  it("shows Zelfconsumptie for self_consume Nu label (not raw snake_case)", () => {
    const html = renderToStaticMarkup(
      createElement(BatteryActionWhy, {
        currentAction: "self_consume",
        reason: REASON,
        dryRun: true,
      }),
    );
    expect(html).toContain("Zelfconsumptie");
    expect(html).not.toContain("self consume");
    expect(html).toContain('data-testid="battery-action-why-text"');
  });

  it("shows Op volle snelheid ontladen for forced dump", () => {
    const html = renderToStaticMarkup(
      createElement(BatteryActionWhy, {
        currentAction: "full_speed_discharge",
        reason: REASON,
        dryRun: false,
      }),
    );
    expect(html).toContain("Op volle snelheid ontladen");
    expect(html).toMatch(/volle snelheid/i);
  });
});
