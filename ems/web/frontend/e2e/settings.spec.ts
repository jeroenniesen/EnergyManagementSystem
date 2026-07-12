import { expect, test } from "@playwright/test";

// A small stand-in schema for the mocked-API tests (so they never touch the shared dev DB
// or race other workers). The real schema is exercised by the read-only test below.
const SCHEMA = [
  {
    key: "planner.charge_slots", label: "Charge window", type: "int", default: 12,
    group: "planner", help: "", min: 1, max: 96, options: null, step: null, unit: "slots",
  },
  {
    key: "ui.theme", label: "Theme", type: "enum", default: "auto",
    group: "ui", help: "", min: null, max: null,
    options: ["auto", "dark", "light"], step: null, unit: "",
  },
];
const BASE_VALUES: Record<string, number | string> = {
  "planner.charge_slots": 12,
  "ui.theme": "auto",
};

test.describe("EMS settings", () => {
  test("GET /api/settings returns schema and effective values", async ({ request }) => {
    const r = await request.get("/api/settings");
    expect(r.ok()).toBeTruthy();
    const b = await r.json();
    expect(Array.isArray(b.schema)).toBeTruthy();
    expect(b.schema.some((f: { key: string }) => f.key === "ui.theme")).toBeTruthy();
    // Keys contain dots; toHaveProperty would treat them as nested paths, so check keys directly.
    expect(Object.keys(b.values)).toContain("planner.charge_slots");
  });

  test("the solar-confidence planner setting renders as a drag slider", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-planner").click();
    const field = page.getByTestId("field-planner.solar_confidence");
    await expect(field).toBeVisible();
    // It's a drag slider (range input) with a live read-out, not a plain number box.
    await expect(field.locator("input[type=range]")).toBeVisible();
  });

  test("Settings tab renders grouped basic settings; advanced is hidden by default", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    const s = page.getByTestId("settings");
    await expect(s).toBeVisible();
    // Group sections are collapsible (headers always visible); expand the ones we check.
    await expect(s).toContainText("Connection");
    await expect(s).toContainText("Energy meters (HomeWizard)");
    await page.getByTestId("group-meters").click();
    await page.getByTestId("group-ui").click();
    await expect(page.getByTestId("field-meters.p1_ip")).toBeVisible();
    await expect(page.getByTestId("field-ui.theme")).toBeVisible();
    // Advanced planner economics are hidden until the toggle is enabled.
    await expect(page.getByTestId("field-planner.round_trip_efficiency")).toHaveCount(0);
    await page.getByTestId("advanced-toggle").check();
    await page.getByTestId("group-planner").click();
    await expect(page.getByTestId("field-planner.round_trip_efficiency")).toBeVisible();
    // The dashboard panels must be hidden while the Settings view is active.
    await expect(page.getByTestId("status-grid")).toHaveCount(0);
  });

  test("device IPs and the Tibber token are configurable (grouped by type)", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    // Devices are no longer hard-wired — they're editable fields grouped by type (expand each).
    await page.getByTestId("group-meters").click();
    await page.getByTestId("group-battery").click();
    await page.getByTestId("group-prices").click();
    await expect(page.getByTestId("field-meters.p1_ip")).toBeVisible();
    await expect(page.getByTestId("field-battery.indevolt_ip")).toBeVisible();
    await expect(page.getByTestId("field-prices.tibber_token")).toBeVisible();
    // Connection fields are flagged as needing a restart.
    await expect(page.getByTestId("field-meters.p1_ip")).toContainText("restart");
  });

  test("operational-mode toggle is present, off by default, and flagged restart", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-control").click();
    const op = page.getByTestId("field-control.operational");
    await expect(op).toBeVisible();
    await expect(op).toContainText("control the battery");
    await expect(op).toContainText("restart");
    // Default OFF (dry-run) — the checkbox is unchecked, so the battery is never commanded.
    await expect(op.locator("#set-control\\.operational")).not.toBeChecked();
  });

  test("changing a planner setting shows a before/after plan-impact preview", async ({ page }) => {
    const SCHEMA_ADV = [
      {
        key: "planner.charge_slots", label: "Charge window", type: "int", default: 12,
        group: "planner", help: "", min: 1, max: 96, options: null, step: null, unit: "slots",
        advanced: true, applies: "live",
      },
    ];
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ schema: SCHEMA_ADV, values: { "planner.charge_slots": 12 } }),
        });
      } else {
        await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      }
    });
    await page.route("**/api/plan-preview", async (route) => {
      const body = JSON.parse(route.request().postData() || "{}");
      const proposed = body["planner.charge_slots"] ?? 12;
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          current: { summary: "charge 12×15m", savings_eur: 1.2, charge_slots: 12, discharge_slots: 8 },
          proposed: {
            summary: `charge ${proposed}×15m`, savings_eur: 0.7,
            charge_slots: proposed, discharge_slots: 8,
          },
        }),
      });
    });
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("advanced-toggle").check();
    await page.getByTestId("group-planner").click();
    const input = page.locator("#set-planner\\.charge_slots");
    await input.fill("4");
    await input.blur();
    // The impact panel appears (debounced) and shows the proposed plan.
    await expect(page.getByTestId("settings-impact")).toBeVisible();
    await expect(page.getByTestId("impact-proposed")).toContainText("charge 4×15m");
  });

  test("editing enables Save and shows a saved confirmation", async ({ page }) => {
    let saved: Record<string, unknown> = {};
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        saved = JSON.parse(route.request().postData() || "{}");
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ values: { ...BASE_VALUES, ...saved } }),
        });
      } else {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ schema: SCHEMA, values: BASE_VALUES }),
        });
      }
    });
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-ui").click();
    const save = page.getByTestId("settings-save");
    await expect(save).toBeDisabled(); // nothing changed yet
    await page.locator("#set-ui\\.theme").selectOption("dark");
    await expect(save).toBeEnabled();
    await save.click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    expect(saved["ui.theme"]).toBe("dark");
  });

  test("the Car group shows the 7-day weekly charge schedule editor", async ({ page }) => {
    const defaultSchedule = JSON.stringify({
      mon: { enabled: false, min_pct: 80, ready_by: "07:30" },
      tue: { enabled: false, min_pct: 80, ready_by: "07:30" },
      wed: { enabled: false, min_pct: 80, ready_by: "07:30" },
      thu: { enabled: false, min_pct: 80, ready_by: "07:30" },
      fri: { enabled: false, min_pct: 80, ready_by: "07:30" },
      sat: { enabled: false, min_pct: 80, ready_by: "07:30" },
      sun: { enabled: false, min_pct: 80, ready_by: "07:30" },
    });
    const SCHEMA_EV = [
      {
        key: "ev.schedule", label: "Weekly charge schedule", type: "text", default: defaultSchedule,
        group: "ev", help: "Weekly minimum charge schedule — edited with the schedule editor below.",
        min: null, max: null, options: null, step: null, unit: "", advanced: false, applies: "live",
      },
    ];
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ schema: SCHEMA_EV, values: { "ev.schedule": defaultSchedule } }),
        });
      } else {
        await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      }
    });
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-ev").click();
    await expect(page.getByTestId("field-ev.schedule")).toBeVisible();
    // All 7 days render as a row (enable toggle + min% + ready-by), not a raw JSON textbox.
    for (const day of ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]) {
      await expect(page.getByTestId(`ev-schedule-${day}-enabled`)).toBeVisible();
      await expect(page.getByTestId(`ev-schedule-${day}-min-pct`)).toHaveValue("80");
      await expect(page.getByTestId(`ev-schedule-${day}-ready-by`)).toHaveValue("07:30");
    }
  });

  test("toggling a schedule day and saving POSTs a valid ev.schedule JSON string", async ({
    page,
  }) => {
    const defaultSchedule = JSON.stringify({
      mon: { enabled: false, min_pct: 80, ready_by: "07:30" },
      tue: { enabled: false, min_pct: 80, ready_by: "07:30" },
      wed: { enabled: false, min_pct: 80, ready_by: "07:30" },
      thu: { enabled: false, min_pct: 80, ready_by: "07:30" },
      fri: { enabled: false, min_pct: 80, ready_by: "07:30" },
      sat: { enabled: false, min_pct: 80, ready_by: "07:30" },
      sun: { enabled: false, min_pct: 80, ready_by: "07:30" },
    });
    const SCHEMA_EV = [
      {
        key: "ev.schedule", label: "Weekly charge schedule", type: "text", default: defaultSchedule,
        group: "ev", help: "Weekly minimum charge schedule — edited with the schedule editor below.",
        min: null, max: null, options: null, step: null, unit: "", advanced: false, applies: "live",
      },
    ];
    let saved: Record<string, unknown> = {};
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        saved = JSON.parse(route.request().postData() || "{}");
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ values: { "ev.schedule": saved["ev.schedule"] } }),
        });
      } else {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ schema: SCHEMA_EV, values: { "ev.schedule": defaultSchedule } }),
        });
      }
    });
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-ev").click();
    const save = page.getByTestId("settings-save");
    await expect(save).toBeDisabled();
    await page.getByTestId("ev-schedule-mon-enabled").check();
    await page.getByTestId("ev-schedule-mon-ready-by").fill("06:15");
    await page.getByTestId("ev-schedule-mon-min-pct").fill("90");
    await expect(save).toBeEnabled();
    await save.click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();

    expect(typeof saved["ev.schedule"]).toBe("string");
    const posted = JSON.parse(saved["ev.schedule"] as string);
    expect(posted.mon).toEqual({ enabled: true, min_pct: 90, ready_by: "06:15" });
    // Untouched days keep their (valid) default shape.
    expect(posted.tue).toEqual({ enabled: false, min_pct: 80, ready_by: "07:30" });
    expect(Object.keys(posted).sort()).toEqual(
      ["fri", "mon", "sat", "sun", "thu", "tue", "wed"],
    );
  });

  test("an invalid value surfaces a per-field error from the API", async ({ page }) => {
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 422, contentType: "application/json",
          body: JSON.stringify({
            detail: "invalid settings",
            errors: { "ui.theme": "must be one of: auto, dark, light" },
          }),
        });
      } else {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ schema: SCHEMA, values: BASE_VALUES }),
        });
      }
    });
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-ui").click();
    await page.locator("#set-ui\\.theme").selectOption("dark");
    await page.getByTestId("settings-save").click();
    await expect(page.getByTestId("err-ui.theme")).toBeVisible();
  });
});
