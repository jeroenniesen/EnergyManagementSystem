import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

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

test.describe("Consumers start advice (#333)", () => {
  test("shows start advice reason on Insights", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.route("**/api/consumers/start-advice**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          title: "Start window advice",
          enabled: true,
          dry_run: true,
          live_armed: false,
          summary: "1 start window from day-ahead prices.",
          advice: [
            {
              device_id: "mock-washer-1",
              device_name: "Mock washer",
              kind: "washer",
              eligible: true,
              window: {
                start: "2026-07-11T10:00:00+00:00",
                end: "2026-07-11T11:00:00+00:00",
                slots: 4,
                avg_eur_per_kwh: 0.06,
                est_cost_eur: 0.06,
                assumed_kwh: 1.0,
                reason:
                  "Best 1h window from the day-ahead prices (avg €0.06/kWh; 2 slot(s) use solar export value, assuming 1 kWh).",
              },
              skip_reason: null,
            },
          ],
          footnote:
            "Advice only — nothing changes automatically. EMS does not start or stop appliances from this page.",
        }),
      }),
    );
    await page.route("**/api/consumers**", (route) => {
      if (route.request().url().includes("/start-advice")) {
        return route.fallback();
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          title: "Appliances",
          summary: "1 selected appliance.",
          read_only: true,
          devices: [
            {
              id: "mock-washer-1",
              name: "Mock washer",
              kind: "washer",
              integration: "mock",
              opted_in: true,
              status: {
                state: "idle",
                state_label: "Idle — ready when you are",
                power_w: 450,
                energy_kwh: 1.25,
                can_start: true,
                observed_at: null,
                detail: null,
              },
            },
          ],
        }),
      });
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("insights")).toBeVisible();
    const tip = page.getByTestId("consumer-mock-washer-1-start-advice");
    await expect(tip).toBeVisible();
    await expect(tip).toContainText("Suggested start");
    await expect(tip.getByText(/solar export value/i)).toBeVisible();
    await expect(tip.getByText(/assuming 1 kWh/i)).toBeVisible();
  });

  test("appliances card passes axe on Insights", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.route("**/api/consumers/start-advice**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          enabled: true,
          dry_run: true,
          live_armed: false,
          summary: "1 start window",
          advice: [
            {
              device_id: "mock-washer-1",
              device_name: "Mock washer",
              kind: "washer",
              eligible: true,
              window: {
                start: "2026-07-11T10:00:00+00:00",
                end: "2026-07-11T11:00:00+00:00",
                slots: 4,
                avg_eur_per_kwh: 0.08,
                est_cost_eur: 0.08,
                assumed_kwh: 1.0,
                reason: "Best 1h window (avg €0.08/kWh, assuming 1 kWh).",
              },
              skip_reason: null,
            },
          ],
          footnote: "Advice only — nothing changes automatically.",
        }),
      }),
    );
    await page.route("**/api/consumers**", (route) => {
      if (route.request().url().includes("/start-advice")) {
        return route.fallback();
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          title: "Appliances",
          summary: "1 selected appliance.",
          read_only: true,
          devices: [
            {
              id: "mock-washer-1",
              name: "Mock washer",
              kind: "washer",
              integration: "mock",
              opted_in: true,
              status: {
                state: "idle",
                state_label: "Idle",
                power_w: null,
                energy_kwh: null,
                can_start: true,
                observed_at: null,
                detail: null,
              },
            },
          ],
        }),
      });
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("insights")).toBeVisible();
    await expect(page.getByTestId("consumers-card")).toBeVisible();
    const results = await new AxeBuilder({ page })
      .include("[data-testid='consumers-card']")
      .analyze();
    expect(results.violations).toEqual([]);
  });
});
