import { expect, test } from "@playwright/test";

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

test.describe("Insights", () => {
  test("shows the three scores and the energy-flow amounts", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("insights")).toBeVisible();
    await expect(page.getByTestId("insights-headline")).toContainText("80% on your own");
    await expect(page.getByTestId("score-grid")).toBeVisible();
    // Three self-explaining 0-100 tiles.
    await expect(page.getByTestId("score-self_consumption-value")).toContainText("80");
    await expect(page.getByTestId("score-co2-value")).toContainText("60");
    await expect(page.getByTestId("score-best_price-value")).toContainText("75");
    await expect(page.getByTestId("score-co2")).toContainText("Avoided 60%");
    // Screen-reader label states the score in words (not just the visual "60/100").
    await expect(page.getByTestId("score-co2")).toHaveAttribute("aria-label", /60 out of 100/);
    // The flow amounts the user asked for (from solar/grid/battery → house/car).
    const flow = page.getByTestId("flow-report");
    await expect(flow).toContainText("Solar");
    await expect(flow).toContainText("Car");
    await expect(flow).toContainText("10.0 kWh"); // solar total
    await expect(page.getByTestId("error")).toHaveCount(0);
  });

  test("the home screen shows today's score rings that open Insights", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.goto("/");
    await expect(page.getByTestId("home-scores")).toBeVisible();
    await expect(page.getByTestId("ring-self_consumption")).toBeVisible();
    await expect(page.getByTestId("ring-co2")).toBeVisible();
    await expect(page.getByTestId("ring-best_price")).toBeVisible();
    await expect(page.getByTestId("ring-co2")).toContainText("60"); // the score value in the ring
    // The reflective layer: a warm day summary + a band-aware caption under each ring.
    const summary = page.getByTestId("home-scores-summary");
    await expect(summary).toHaveAttribute("data-tone", "good"); // 80/60/75 → solid, not brilliant
    await expect(summary).toContainText("solid energy day");
    await expect(page.getByTestId("ring-self_consumption")).toContainText("Mostly your own sun");
    await expect(page.getByTestId("ring-co2")).toContainText("Cleaner than the grid");
    // Tapping a ring opens the Insights tab.
    await page.getByTestId("ring-self_consumption").click();
    await expect(page.getByTestId("insights")).toBeVisible();
  });

  test("a clean day is celebrated (all scores high → a brilliant-day summary)", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          ...REPORT,
          scores: REPORT.scores.map((s) => ({ ...s, value: 90 })),
        }),
      }),
    );
    await page.goto("/");
    const summary = page.getByTestId("home-scores-summary");
    await expect(summary).toHaveAttribute("data-tone", "great");
    await expect(summary).toContainText("brilliant day");
    // Every ring reads as a win.
    await expect(page.getByTestId("ring-self_consumption")).toContainText("Mostly your own sun");
    await expect(page.getByTestId("ring-best_price")).toContainText("Bought at the right times");
  });

  test("the period picker switches windows", async ({ page }) => {
    await page.route("**/api/report**", (route) => {
      const period = new URL(route.request().url()).searchParams.get("period") ?? "day";
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, period, label: period === "month" ? "2026-06" : "2026-06-28" }),
      });
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await page.getByTestId("period-month").click();
    await expect(page.getByTestId("period-month")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("insights-label")).toHaveText("2026-06");
  });

  test("shows an empty state when no energy is recorded", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, flows: { ...REPORT.flows, has_data: false } }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("insights-empty")).toBeVisible();
    await expect(page.getByTestId("score-grid")).toHaveCount(0);
  });

  test("flags a car-guard leak when the battery fed the car", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          ...REPORT,
          flows: { ...REPORT.flows, battery_to_car: 0.4, car_guard_leak_kwh: 0.4 },
        }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("leak-warn")).toContainText("into the car");
  });
});
