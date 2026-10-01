import { describe, expect, test } from "vitest";

import {
  DRY_RUN_CAUSE_FALLBACK,
  DRY_RUN_CAUSE_LABEL,
  HOME_HEADLINE,
  OUTCOME_LABEL,
  PLAN_ACTION_META,
  homeConfidenceReasonNl,
  homeHeadlineNl,
} from "./labels";

describe("homeHeadlineNl", () => {
  test("maps the small readiness.home_state headline set to Dutch", () => {
    expect(homeHeadlineNl("Needs attention — battery or meter data is unavailable")).toBe(
      HOME_HEADLINE.needsAttention,
    );
    expect(homeHeadlineNl("You're in manual control")).toBe(HOME_HEADLINE.manualControl);
    expect(homeHeadlineNl("All good — the battery is running the house")).toBe(
      `${HOME_HEADLINE.allGood} — ${HOME_HEADLINE.batteryRunningHouse}`,
    );
    expect(homeHeadlineNl("Watching — the battery is safe")).toBe(
      `${HOME_HEADLINE.watching} — ${HOME_HEADLINE.batterySafe}`,
    );
    expect(homeHeadlineNl("Watching your home")).toBe(
      `${HOME_HEADLINE.watching} — je huis`,
    );
    expect(homeHeadlineNl("Running the house on your battery")).toBe(
      HOME_HEADLINE.runningHouse,
    );
    expect(homeHeadlineNl("Topping up the battery")).toBe(HOME_HEADLINE.toppingUp);
    expect(homeHeadlineNl("Holding the battery for later")).toBe(HOME_HEADLINE.holding);
  });

  test("does not leave raw English in the hero fold", () => {
    expect(homeHeadlineNl("Some unknown English headline")).toBe(HOME_HEADLINE.watching);
    expect(homeHeadlineNl("All good — mystery phrase")).toBe(HOME_HEADLINE.allGood);
  });
});

describe("homeConfidenceReasonNl", () => {
  test("translates B-68 confidence.py reasons", () => {
    expect(
      homeConfidenceReasonNl("Safety fallback active — EMS is holding, not planning."),
    ).toMatch(/Veiligheidsfallback/);
    expect(
      homeConfidenceReasonNl(
        "Still learning your roof — under 2.0 days of forecast evidence so far.",
      ),
    ).toMatch(/Nog je dak/);
    expect(
      homeConfidenceReasonNl("Still learning your roof."),
    ).toMatch(/Nog je dak/);
    expect(
      homeConfidenceReasonNl(
        "Fresh data, calibrated forecast, battery responding — nothing is holding this plan back.",
      ),
    ).toMatch(/Actuele data/);
  });

  test("hides unmapped English rather than flip-flopping locale", () => {
    expect(homeConfidenceReasonNl("Completely novel English reason about the battery.")).toBeNull();
  });
});

describe("B-98 fix nits", () => {
  test("dry-run cause badge is Dutch beside Alleen kijken", () => {
    expect(DRY_RUN_CAUSE_LABEL.settings_dry_run).toBe("Instellingen alleen kijken");
    expect(DRY_RUN_CAUSE_LABEL.dry_run).toBe("alleen kijken");
    expect(DRY_RUN_CAUSE_LABEL.no_live_prices).toBe("geen live prijzen");
    expect(DRY_RUN_CAUSE_FALLBACK).toBe("oorzaak");
  });

  test("PlanStory action labels align with Nu:", () => {
    expect(PLAN_ACTION_META.solar_charge.label).toBe("Laden van zonnepanelen");
    expect(PLAN_ACTION_META.self_consume.label).toBe("Zelfconsumptie");
  });

  test("cap_reached uses Dagelijkse", () => {
    expect(OUTCOME_LABEL.cap_reached).toBe("Dagelijkse schakellimiet bereikt");
  });
});
