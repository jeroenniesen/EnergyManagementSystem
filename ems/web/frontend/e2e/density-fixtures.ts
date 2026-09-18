import { expect, type Page } from "@playwright/test";

const REPORT = {
  period: "day",
  window_start: "2026-06-28T00:00:00+00:00",
  window_end: "2026-06-29T00:00:00+00:00",
  label: "2026-06-28",
  partial: false,
  flows: {
    date: "2026-06-28", has_data: true, partial: false,
    solar_to_home: 4, solar_to_car: 1, solar_to_battery: 3, solar_to_grid: 2,
    grid_to_home: 1, grid_to_car: 0.5, grid_to_battery: 0.5,
    battery_to_home: 2.5, battery_to_car: 0, battery_to_grid: 0,
    solar_kwh: 10, grid_import_kwh: 2, grid_export_kwh: 2,
    battery_charge_kwh: 3.5, battery_discharge_kwh: 2.5, home_kwh: 7.5, car_kwh: 1.5,
    self_sufficiency_pct: 80, solar_self_consumption_pct: 80, car_guard_leak_kwh: 0,
  },
  scores: [
    { key: "self_consumption", label: "Self-consumption", value: 80, raw: 80, unit: "%",
      explanation: "Kept 80% of your solar on-site; exported 2.0 kWh you couldn't use or store." },
    { key: "co2", label: "CO₂", value: 60, raw: 1.6, unit: "kg",
      explanation: "Avoided 60% of a no-solar home's CO₂ (2 kg vs 4 kg)." },
    { key: "best_price", label: "Best price", value: 75, raw: 0.13, unit: "€/kWh",
      explanation: "Imported at €0.13/kWh vs the period's €0.08–€0.30 range; ≈ €0.30 saved." },
  ],
};

// Density is a property of a rendered scenario, not the recorder's current sample count.
// Pin every independently loaded Insights panel before measuring the settled page.
export async function mockInsightsDensity(page: Page) {
  await page.route("**/api/report**", route => route.fulfill({ json: REPORT }));
  await page.route("**/api/finance**", route => route.fulfill({ json: {
    period: "day", label: "2026-06-28", partial: false, days: [],
    totals: { saved_eur: 1.2, grid_cost_eur: 2.1, battery_cost_eur: .2,
      days_with_data: 1, days_with_prices: 1 },
  } }));
  await page.route("**/api/digest**", route => route.fulfill({ json: {
    week_label: "Week of 2026-06-22", headline: "A steady energy week.",
    saved_eur: 4.8, best_day: null, self_sufficiency_pct: 80, solar_kwh: 30,
    co2_avoided_note: null, actions: { mode_switches: 0, negative_soaks: 0, overrides: 0 },
    tweak: null, days_measured: 7, days_total: 7,
  } }));
  await page.route("**/api/counterfactual**", route => route.fulfill({ json: {
    window: null, days_used: 0, days_skipped: 0, scenarios: {},
    deltas: { planner_vs_no_battery: null, planner_vs_auto: null }, note: "No measured days yet.",
  } }));
}

export async function waitForInsightsDensity(page: Page) {
  await expect(page.getByTestId("score-grid")).toBeVisible();
  await expect(page.getByTestId("fin-saved")).toContainText("€1.20");
  await expect(page.getByTestId("week-digest-headline")).toHaveText("A steady energy week.");
}
