import { describe, expect, test } from "vitest";

import { buildHeroSynthesis, isPlannerJargon, localizeHeroPhrase } from "./heroSynthesis";

describe("isPlannerJargon", () => {
  test("flags break-even and raw self-consumption operator forms", () => {
    expect(
      isPlannerJargon("self-consumption: €0.45/kWh > break-even €0.32"),
    ).toBe(true);
    expect(isPlannerJargon("no-trade: spread below break-even")).toBe(true);
    expect(isPlannerJargon("discharge: €0.40/kWh > break-even €0.30")).toBe(true);
  });

  test("flags EV-load operator prose", () => {
    expect(isPlannerJargon("EV load expected ~12 kWh (manual day override)")).toBe(
      true,
    );
  });

  test("allows plain homeowner sentences", () => {
    expect(isPlannerJargon("Batterij volgt het huidige plan.")).toBe(false);
    expect(isPlannerJargon("On track for tonight's target.")).toBe(false);
    expect(isPlannerJargon("A brilliant day for clean energy")).toBe(false);
    expect(
      isPlannerJargon("Short of the 88% target with no grid top-up planned."),
    ).toBe(false);
  });
});

describe("localizeHeroPhrase", () => {
  test("maps English on-track / behind / battery-plan phrases to Dutch", () => {
    expect(localizeHeroPhrase("On track for tonight's target.")).toBe(
      "Op schema voor het nachtdoel vanavond",
    );
    expect(localizeHeroPhrase("On track — projected to reach the 88% plan target.")).toBe(
      "Op schema voor het nachtdoel vanavond",
    );
    expect(localizeHeroPhrase("Short of the 88% target with no grid top-up planned.")).toBe(
      "Nog niet op het doel",
    );
    expect(
      localizeHeroPhrase(
        "Behind the 88% target — EMS tops up 2.4 kWh from the grid in the cheapest window before the deadline.",
      ),
    ).toBe("Nog niet op het doel");
    expect(localizeHeroPhrase("Battery is following the current plan.")).toBe(
      "Batterij volgt het huidige plan",
    );
    expect(localizeHeroPhrase("Running the house on your battery tonight.")).toBe(
      "De batterij voedt vanavond de woning",
    );
  });

  test("rejects English score summaries so the hero falls through", () => {
    expect(localizeHeroPhrase("A brilliant day for clean energy")).toBeNull();
    expect(localizeHeroPhrase("A solid energy day — keep it up")).toBeNull();
  });

  test("fails closed on unmapped English current_reason strings", () => {
    expect(localizeHeroPhrase("No current plan or forecast is available.")).toBeNull();
    expect(
      localizeHeroPhrase("Critical sensor, price or forecast data is stale."),
    ).toBeNull();
    expect(localizeHeroPhrase("Plan validation failed.")).toBeNull();
  });
});

describe("buildHeroSynthesis", () => {
  test("prefers Dutch on-track phrasing when the API sends English", () => {
    expect(
      buildHeroSynthesis({
        currentReason: "Battery is following the current plan.",
        onTrackMessage: "On track for tonight's target.",
        scoreSummary: "A brilliant day for clean energy",
      }),
    ).toBe("Op schema voor het nachtdoel vanavond");
  });

  test("skips jargon current_reason and English score — calm Dutch fallback", () => {
    expect(
      buildHeroSynthesis({
        currentReason: "self-consumption: €0.45/kWh > break-even €0.32",
        onTrackMessage: null,
        scoreSummary: "A solid energy day — keep it up",
      }),
    ).toBe("Batterij volgt het huidige plan.");
  });

  test("maps plain English current_reason to Dutch fallback", () => {
    expect(
      buildHeroSynthesis({
        currentReason: "Battery is following the current plan.",
        onTrackMessage: null,
        scoreSummary: null,
      }),
    ).toBe("Batterij volgt het huidige plan");
  });

  test("never joins middot fragments — one sentence only", () => {
    const text = buildHeroSynthesis({
      currentReason: "Battery is following the current plan.",
      onTrackMessage: "On track.",
      scoreSummary: "A brilliant day for clean energy",
    });
    expect(text).not.toContain("·");
    expect(text).toBe("Op schema voor het nachtdoel vanavond");
  });

  test("calm fallback when every candidate is jargon or missing", () => {
    expect(
      buildHeroSynthesis({
        currentReason: "EV load expected ~8 kWh (EV day hint)",
        onTrackMessage: null,
        scoreSummary: null,
      }),
    ).toBe("Batterij volgt het huidige plan.");
  });

  test("unmapped English current_reason falls through to calm Dutch fallback", () => {
    expect(
      buildHeroSynthesis({
        currentReason: "No current plan or forecast is available.",
        onTrackMessage: null,
        scoreSummary: null,
      }),
    ).toBe("Batterij volgt het huidige plan.");
    expect(
      buildHeroSynthesis({
        currentReason: "Plan validation failed.",
        onTrackMessage: null,
        scoreSummary: "Critical sensor, price or forecast data is stale.",
      }),
    ).toBe("Batterij volgt het huidige plan.");
  });

  test("Behind the N% target maps to Dutch behind line", () => {
    expect(
      buildHeroSynthesis({
        currentReason: "Battery is following the current plan.",
        onTrackMessage:
          "Behind the 88% target — EMS tops up 2.4 kWh from the grid in the cheapest window before the deadline.",
        scoreSummary: null,
      }),
    ).toBe("Nog niet op het doel");
  });
});
