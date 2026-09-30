import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DecisionReasonDetails } from "./DecisionReasonDetails";
import type { DecisionReason } from "./decisionWhy";

const REASON: DecisionReason = {
  chosen_window: {
    start: "2026-09-30T01:00:00+00:00",
    end: "2026-09-30T04:00:00+00:00",
    intent: "grid_charge_to_target",
    label: "cheap charge window",
    eur_per_kwh_min: 0.05,
    eur_per_kwh_max: 0.08,
  },
  rejected_alternative: {
    intent: "allow_self_consumption",
    reason: "self-consumption only — rejected in favour of the selected charge window",
    window_start: null,
    window_end: null,
  },
  expected_benefit: { eur: 0.85, summary: "Estimated net benefit ≈ €0.85." },
  risk: { margin_eur_per_kwh: 0.02, summary: "Risk margin €0.020/kWh applied to break-even." },
  safety_constraint: { code: null, message: null, action: "proceed" },
  gates: {
    validator_code: null,
    failsafe: false,
    dwell: false,
    cap_reached: false,
    unconfirmed: false,
  },
  summary: "Charging in the cheap night window.",
};

describe("DecisionReasonDetails (#84 slice 2)", () => {
  it("renders nothing without a reason", () => {
    const html = renderToStaticMarkup(createElement(DecisionReasonDetails, { reason: null }));
    expect(html).toBe("");
  });

  it("renders every structured field from the reason object", () => {
    const html = renderToStaticMarkup(createElement(DecisionReasonDetails, { reason: REASON }));
    expect(html).toContain('data-testid="decision-reason-details"');
    expect(html).toContain("Charging in the cheap night window.");
    expect(html).toContain("cheap charge window");
    expect(html).toContain("self-consumption only");
    expect(html).toContain("Risk margin");
    expect(html).toContain("Proceed — no safety hold");
  });

  it("shows safety pause with finding code from the reason object", () => {
    const paused: DecisionReason = {
      ...REASON,
      safety_constraint: {
        code: "stale_inputs",
        message: "Critical inputs are stale.",
        action: "paused",
      },
      gates: { ...REASON.gates, validator_code: "stale_inputs", failsafe: true },
    };
    const html = renderToStaticMarkup(createElement(DecisionReasonDetails, { reason: paused }));
    expect(html).toContain("Critical inputs are stale.");
    expect(html).toContain("stale_inputs");
    expect(html).toContain("failsafe");
  });
});
