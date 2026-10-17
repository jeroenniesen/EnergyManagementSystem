import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import {
  HOME_NIGHT_RESERVE,
  nightReserveComfortHours,
  nightReserveOverridesLearned,
} from "./labels";
import { NightReserveSettingsHint } from "./Settings";

describe("nightReserveOverridesLearned (#335)", () => {
  test("no badge within 25% relative difference", () => {
    expect(nightReserveOverridesLearned(2, 2.1)).toBe(false);
    expect(nightReserveOverridesLearned(2, 2.5)).toBe(false);
    expect(nightReserveOverridesLearned(2.5, 2)).toBe(false);
  });

  test("badge when difference exceeds 25%", () => {
    expect(nightReserveOverridesLearned(3, 2)).toBe(true);
    expect(nightReserveOverridesLearned(1, 2)).toBe(true);
    expect(nightReserveOverridesLearned(2.51, 2)).toBe(true);
    expect(nightReserveOverridesLearned(1.49, 2)).toBe(true);
    expect(nightReserveOverridesLearned(0.1, 0)).toBe(true);
    expect(nightReserveOverridesLearned(0, 0)).toBe(false);
  });

  test("no badge when learned is unknown", () => {
    expect(nightReserveOverridesLearned(2, null)).toBe(false);
    expect(nightReserveOverridesLearned(2, undefined)).toBe(false);
    expect(nightReserveOverridesLearned(2, -1)).toBe(false);
  });
});

describe("night reserve Settings copy fixtures (#335)", () => {
  const fixtures = [
    {
      name: "default 2 kWh reserve, 6 kWh overnight, learned 5.5",
      nightReserveKwh: 2,
      overnightLoadKwh: 6,
      learnedKwh: 5.5,
      expectExample: "Bij een nacht van 12 uur houdt 2.0 kWh nacht-reserve het huis nog ongeveer 4 uur aan.",
      expectLearned: "Geleerd vannacht ≈ 5.5 kWh",
    },
    {
      name: "higher reserve 4 kWh, learned 2 kWh",
      nightReserveKwh: 4,
      overnightLoadKwh: 8,
      learnedKwh: 2,
      expectExample: "Bij een nacht van 12 uur houdt 4.0 kWh nacht-reserve het huis nog ongeveer 6 uur aan.",
      expectLearned: "Geleerd vannacht ≈ 2.0 kWh",
    },
    {
      name: "learned missing — example still from settings",
      nightReserveKwh: 2,
      overnightLoadKwh: 10,
      learnedKwh: null,
      expectExample: "Bij een nacht van 12 uur houdt 2.0 kWh nacht-reserve het huis nog ongeveer 2 uur aan.",
      expectLearned: "onvoldoende meetdata",
    },
  ] as const;

  test.each(fixtures)("$name", ({ nightReserveKwh, overnightLoadKwh, learnedKwh, expectExample, expectLearned }) => {
    expect(nightReserveComfortHours(nightReserveKwh, overnightLoadKwh)).toBe(
      Number(expectExample.match(/ongeveer (\d+) uur/)?.[1]),
    );
    const html = renderToStaticMarkup(
      createElement(NightReserveSettingsHint, {
        nightReserveKwh,
        overnightLoadKwh,
        learnedKwh,
      }),
    );
    expect(html).toContain(expectExample);
    expect(html).toContain(expectLearned);
    expect(html).not.toMatch(/SoC/i);
  });

  test("min-reserve explainer uses noodrem and comfort without SoC", () => {
    expect(HOME_NIGHT_RESERVE.minReserveExplainer).toMatch(/noodrem/);
    expect(HOME_NIGHT_RESERVE.minReserveExplainer).toMatch(/comfort/);
    expect(HOME_NIGHT_RESERVE.minReserveExplainer).not.toMatch(/SoC/i);
  });
});
