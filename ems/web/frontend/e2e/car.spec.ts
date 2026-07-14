// The Car view (feat/ux-batch-3): the dedicated top-level tab assembling the full car-charging
// card, the weekly-schedule editor + car picker (moved here from Settings), and the charging-
// sessions history table. Also covers the dashboard's COMPACT car card and its link into this view.
import { type Page, expect, test } from "@playwright/test";

// A full car-charging plan, anchored to "now" (floored to the 15-min grid, matching the card's own
// timeline math) so the mocked slots/deadlines land inside the 48h window whenever the suite runs.
function fullPlanBody() {
  const floor15 = (ms: number) => Math.floor(ms / (15 * 60000)) * (15 * 60000);
  const now = Date.now();
  const s1 = floor15(now + 2 * 3600000);
  const s2 = s1 + 15 * 60000;
  const deadlineIso = new Date(floor15(now + 20 * 3600000)).toISOString();
  return {
    enabled: true,
    car_meter_configured: true,
    soc: {
      soc_pct: 42.3, anchor_pct: 40, anchor_ts: new Date(now - 80 * 3600000).toISOString(),
      added_kwh: 1.2, sessions_since_anchor: 1, age_hours: 80, stale: true,
    },
    plan: {
      soc: 42.3,
      deadlines: [
        { ready_by: deadlineIso, min_pct: 80, required_kwh: 3.33, planned_kwh: 3.33,
          pending_kwh: 0, shortfall_kwh: 0, already_met: false, feasible: true },
      ],
      slots: [
        { start: new Date(s1).toISOString(), kw: 7.4, ac_kwh: 1.85, battery_kwh: 1.67,
          eur_per_kwh_effective: 0.18, est_cost_eur: 0.33, solar_surplus: false,
          for_deadline: deadlineIso },
        { start: new Date(s2).toISOString(), kw: 7.4, ac_kwh: 1.85, battery_kwh: 1.67,
          eur_per_kwh_effective: 0.05, est_cost_eur: 0.09, solar_surplus: true,
          for_deadline: deadlineIso },
      ],
      windows: [
        { start: new Date(s1).toISOString(), end: new Date(s2 + 15 * 60000).toISOString(),
          ac_kwh: 3.7, battery_kwh: 3.33, est_cost_eur: 0.42, solar_share_pct: 50,
          reason: "Cheapest slots to reach 80%." },
      ],
      advice: "Plug in this afternoon to reach 80% by tomorrow.",
      negative_price_hint: null,
      total_est_cost_eur: 0.42, total_planned_kwh: 3.33,
    },
  };
}

const DEFAULT_SCHEDULE = JSON.stringify({
  mon: { enabled: false, min_pct: 80, ready_by: "07:30" },
  tue: { enabled: false, min_pct: 80, ready_by: "07:30" },
  wed: { enabled: false, min_pct: 80, ready_by: "07:30" },
  thu: { enabled: false, min_pct: 80, ready_by: "07:30" },
  fri: { enabled: false, min_pct: 80, ready_by: "07:30" },
  sat: { enabled: false, min_pct: 80, ready_by: "07:30" },
  sun: { enabled: false, min_pct: 80, ready_by: "07:30" },
});

const CARS_RESP = {
  brands: ["Skoda", "Tesla"],
  cars: [
    { id: "skoda-enyaq-80", brand: "Skoda", model: "Enyaq 80",
      battery_net_kwh: 77, max_ac_kw: 11, years: "2021–present" },
    { id: "tesla-model-y-long-range", brand: "Tesla", model: "Model Y Long Range",
      battery_net_kwh: 75, max_ac_kw: 11, years: "2020–present" },
    { id: "tesla-model-y-rwd", brand: "Tesla", model: "Model Y RWD",
      battery_net_kwh: 57.5, max_ac_kw: 11, years: "2022–present" },
  ],
};

async function mockCars(page: Page) {
  await page.route("**/api/cars", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CARS_RESP) }));
}

test.describe("Car view", () => {
  test("assembles the full plan card, the schedule editor + picker, and the sessions table",
    async ({ page }) => {
    await page.route("**/api/car/plan", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fullPlanBody()) }));
    await page.route("**/api/car/sessions**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          days: 14,
          sessions: [
            { start: "2026-07-13T23:00:00+02:00", end: "2026-07-14T02:30:00+02:00",
              kwh: 9.5, avg_kw: 3.2, peak_kw: 7.4 },
          ],
        }),
      }));
    await mockCars(page);

    await page.goto("/");
    await page.getByTestId("nav-car").click();
    await expect(page.getByTestId("car-view")).toBeVisible();

    // (a) the full card — windows + the 48h timeline are present (full, not compact).
    await expect(page.getByTestId("car-card")).toBeVisible();
    await expect(page.getByTestId("car-advice")).toContainText("Plug in this afternoon");
    await expect(page.getByTestId("car-window-row").first()).toContainText("3.3 kWh");
    await expect(page.getByTestId("car-timeline-cell")).toHaveCount(192);

    // (b) the schedule editor + car picker, moved here from Settings.
    await expect(page.getByTestId("ev-schedule-editor")).toBeVisible();
    await expect(page.getByTestId("ev-schedule-mon-enabled")).toBeVisible();
    await expect(page.getByTestId("car-picker")).toBeVisible();

    // (c) the sessions history table, formatted "day time–time · kWh · avg kW".
    const row = page.getByTestId("car-session-row").first();
    await expect(row).toContainText("9.5 kWh");
    await expect(row).toContainText("avg 3.2 kW");
  });

  test("the sessions table shows an honest empty state", async ({ page }) => {
    await page.route("**/api/car/sessions**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ sessions: [], days: 14 }) }));
    await mockCars(page);
    await page.goto("/");
    await page.getByTestId("nav-car").click();
    await expect(page.getByTestId("car-sessions-empty")).toContainText(
      "No charging sessions detected in the last 14 days");
  });

  test("shows the 7-day weekly schedule editor (moved from Settings)", async ({ page }) => {
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ status: 200, contentType: "application/json",
          body: JSON.stringify({ schema: [], values: { "ev.schedule": DEFAULT_SCHEDULE } }) });
      } else {
        await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      }
    });
    await mockCars(page);
    await page.goto("/");
    await page.getByTestId("nav-car").click();
    await expect(page.getByTestId("ev-schedule-editor")).toBeVisible();
    // All 7 days render as a row (enable toggle + min% + ready-by), not a raw JSON textbox.
    for (const day of ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]) {
      await expect(page.getByTestId(`ev-schedule-${day}-enabled`)).toBeVisible();
      await expect(page.getByTestId(`ev-schedule-${day}-min-pct`)).toHaveValue("80");
      await expect(page.getByTestId(`ev-schedule-${day}-ready-by`)).toHaveValue("07:30");
    }
  });

  test("editing the schedule in the Car view saves a valid ev.schedule via the settings POST",
    async ({ page }) => {
    let saved: Record<string, unknown> = {};
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        saved = JSON.parse(route.request().postData() || "{}");
        await route.fulfill({ status: 200, contentType: "application/json",
          body: JSON.stringify({ values: { "ev.schedule": saved["ev.schedule"] } }) });
      } else {
        await route.fulfill({ status: 200, contentType: "application/json",
          body: JSON.stringify({ schema: [], values: { "ev.schedule": DEFAULT_SCHEDULE } }) });
      }
    });
    await mockCars(page);
    await page.goto("/");
    await page.getByTestId("nav-car").click();

    // The Car view has its OWN sticky save bar; it only appears once something changes.
    await expect(page.getByTestId("car-save")).toHaveCount(0);
    await page.getByTestId("ev-schedule-mon-enabled").check();
    await page.getByTestId("ev-schedule-mon-ready-by").fill("06:15");
    await page.getByTestId("ev-schedule-mon-min-pct").fill("90");
    const save = page.getByTestId("car-save");
    await expect(save).toBeEnabled();
    await save.click();
    await expect(page.getByTestId("car-saved")).toBeVisible();

    expect(typeof saved["ev.schedule"]).toBe("string");
    const posted = JSON.parse(saved["ev.schedule"] as string);
    expect(posted.mon).toEqual({ enabled: true, min_pct: 90, ready_by: "06:15" });
    expect(posted.tue).toEqual({ enabled: false, min_pct: 80, ready_by: "07:30" });
  });

  test("the brand/model picker autofills battery capacity from /api/cars", async ({ page }) => {
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ status: 200, contentType: "application/json",
          body: JSON.stringify({ schema: [], values: { "ev.car_id": "", "ev.battery_kwh": 57.5 } }) });
      } else {
        await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      }
    });
    await mockCars(page);
    await page.goto("/");
    await page.getByTestId("nav-car").click();

    const brandSelect = page.getByTestId("car-brand-select");
    const modelSelect = page.getByTestId("car-model-select");
    await expect(brandSelect).toBeVisible();
    await expect(brandSelect.locator("option")).toContainText(["Custom", "Skoda", "Tesla"]);

    await brandSelect.selectOption("Tesla");
    await modelSelect.selectOption("tesla-model-y-long-range");
    // Autofills battery capacity from the picked model + shows its specs.
    await expect(page.locator("#set-ev\\.battery_kwh")).toHaveValue("75");
    await expect(page.getByTestId("car-picker-specs")).toContainText("75 kWh usable");
    await expect(page.getByTestId("car-config-ac-hint")).toContainText("11 kW");

    // Picking "Custom" clears the car; the (overridden) capacity value stays.
    await brandSelect.selectOption("");
    await expect(page.getByTestId("car-picker-specs")).toHaveCount(0);
    await expect(page.locator("#set-ev\\.battery_kwh")).toHaveValue("75");
  });

  test("the dashboard shows a COMPACT car card that links into the Car view", async ({ page }) => {
    await page.route("**/api/car/plan", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fullPlanBody()) }));
    await mockCars(page);
    await page.goto("/");

    // Compact: SoC + deadline + advice + "Open Car →" — but NOT the windows or the 48h timeline.
    const card = page.getByTestId("car-card");
    await expect(card).toHaveAttribute("data-compact", "true");
    await expect(page.getByTestId("car-soc-value")).toHaveText("42.3%");
    await expect(page.getByTestId("car-advice")).toBeVisible();
    await expect(page.getByTestId("car-window-row")).toHaveCount(0);
    await expect(page.getByTestId("car-timeline-cell")).toHaveCount(0);

    // "Open Car →" navigates to the Car view, where the full detail appears.
    await page.getByTestId("car-open-full").click();
    await expect(page.getByTestId("nav-car")).toHaveClass(/nav-active/);
    await expect(page.getByTestId("car-view")).toBeVisible();
    await expect(page.getByTestId("car-window-row").first()).toBeVisible();
  });
});
