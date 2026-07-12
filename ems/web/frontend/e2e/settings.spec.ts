import { expect, test } from "@playwright/test";

// A small stand-in schema for the mocked-API tests (so they never touch the shared dev DB
// or race other workers). The real schema is exercised by the read-only tests below.
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
    // Sidebar → open the Planner section in the content pane (one section at a time now).
    await page.getByTestId("group-planner").click();
    const field = page.getByTestId("field-planner.solar_confidence");
    await expect(field).toBeVisible();
    // It's a drag slider (range input) with a live read-out, not a plain number box.
    await expect(field.locator("input[type=range]")).toBeVisible();
  });

  test("the sidebar groups sections; a section opens in the content pane on click", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    const s = page.getByTestId("settings");
    await expect(s).toBeVisible();
    // The sidebar lists the section titles (grouped under the three intent headers).
    await expect(s).toContainText("Connection");
    await expect(s).toContainText("Energy meters (HomeWizard)");
    await expect(s).toContainText("Your setup");
    await expect(s).toContainText("How it runs");
    // Opening a section shows its fields in the (single-column) content pane.
    await page.getByTestId("group-meters").click();
    await expect(page.getByTestId("field-meters.p1_ip")).toBeVisible();
    await page.getByTestId("group-ui").click();
    await expect(page.getByTestId("field-ui.theme")).toBeVisible();
    // The dashboard panels must be hidden while the Settings view is active.
    await expect(page.getByTestId("status-grid")).toHaveCount(0);
  });

  test("advanced fields hide behind an in-place Advanced divider (no global toggle)", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-planner").click();
    // The advanced planner economics are collapsed by default...
    await expect(page.getByTestId("field-planner.round_trip_efficiency")).toHaveCount(0);
    // ...and revealed by the section's own Advanced disclosure.
    await page.getByTestId("settings-advanced-toggle").click();
    await expect(page.getByTestId("settings-advanced-body")).toBeVisible();
    await expect(page.getByTestId("field-planner.round_trip_efficiency")).toBeVisible();
  });

  test("device IPs and the Tibber token are configurable (grouped by type)", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    // Devices are editable fields grouped by type — open each section from the sidebar.
    await page.getByTestId("group-meters").click();
    await expect(page.getByTestId("field-meters.p1_ip")).toBeVisible();
    // Connection fields are flagged as needing a restart.
    await expect(page.getByTestId("field-meters.p1_ip")).toContainText("restart");
    await page.getByTestId("group-battery").click();
    await expect(page.getByTestId("field-battery.indevolt_ip")).toBeVisible();
    await page.getByTestId("group-prices").click();
    await expect(page.getByTestId("field-prices.tibber_token")).toBeVisible();
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
    // Default OFF (dry-run) — the switch is unchecked, so the battery is never commanded.
    await expect(op.locator("#set-control\\.operational")).not.toBeChecked();
  });

  test("a boolean setting renders as a toggle switch that stays keyboard-togglable", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-strategy").click();
    const sw = page.locator("#set-strategy\\.summer_grid_topup");
    await expect(sw).toBeVisible();
    // Styled as a switch (role=switch) but still an <input type=checkbox> under the hood.
    await expect(sw).toHaveAttribute("role", "switch");
    await expect(sw).toHaveAttribute("type", "checkbox");
    await expect(sw).toBeChecked(); // default ON
    await sw.click();
    await expect(sw).not.toBeChecked(); // still togglable
  });

  test("search filters the sidebar and jumps to (and highlights) the matching field", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("settings-search").fill("efficiency");
    // The Planner section surfaces as a match, with a matched-field count.
    await expect(page.getByTestId("nav-count-planner")).toHaveText("1");
    // Selecting the result opens that section (auto-revealing its Advanced group) and highlights
    // the matched field — even though round_trip_efficiency is an advanced field.
    await page.getByTestId("group-planner").click();
    const field = page.getByTestId("field-planner.round_trip_efficiency");
    await expect(field).toBeVisible();
    await expect(
      page.locator(".field-highlight").getByTestId("field-planner.round_trip_efficiency"),
    ).toBeVisible();
    // Esc clears the search.
    await page.getByTestId("settings-search").press("Escape");
    await expect(page.getByTestId("settings-search")).toHaveValue("");
  });

  test("the sticky save bar appears on edit with the change count and saves", async ({ page }) => {
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
    // No save bar until something is dirty.
    await expect(page.getByTestId("settings-savebar")).toHaveCount(0);
    await page.locator("#set-ui\\.theme").selectOption("dark");
    const bar = page.getByTestId("settings-savebar");
    await expect(bar).toBeVisible();
    await expect(bar).toContainText("1 unsaved change");
    await page.getByTestId("settings-save").click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    expect(saved["ui.theme"]).toBe("dark");
  });

  test("Discard reverts pending edits and hides the save bar", async ({ page }) => {
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ schema: SCHEMA, values: BASE_VALUES }),
        });
      } else {
        await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      }
    });
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-ui").click();
    await page.locator("#set-ui\\.theme").selectOption("dark");
    await expect(page.getByTestId("settings-savebar")).toBeVisible();
    await page.getByTestId("settings-discard").click();
    await expect(page.getByTestId("settings-savebar")).toHaveCount(0);
    await expect(page.locator("#set-ui\\.theme")).toHaveValue("auto");
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
    await page.getByTestId("group-planner").click();
    // charge_slots is advanced here — reveal it, then edit.
    await page.getByTestId("settings-advanced-toggle").click();
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
    // The Save button lives in the sticky bar, which only exists once something is dirty.
    await expect(page.getByTestId("settings-save")).toHaveCount(0);
    await page.locator("#set-ui\\.theme").selectOption("dark");
    const save = page.getByTestId("settings-save");
    await expect(save).toBeEnabled();
    await save.click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    expect(saved["ui.theme"]).toBe("dark");
  });

  test("mobile: the sidebar is a drill-in list (list → section → back)", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    // Starts on the section list: the sidebar is shown, the content pane hidden.
    await expect(page.getByTestId("group-ui")).toBeVisible();
    await expect(page.getByTestId("settings-back")).toBeHidden();
    // Drill into a section: the content pane shows, the back button appears.
    await page.getByTestId("group-ui").click();
    await expect(page.getByTestId("field-ui.theme")).toBeVisible();
    await expect(page.getByTestId("settings-back")).toBeVisible();
    // Back returns to the list.
    await page.getByTestId("settings-back").click();
    await expect(page.getByTestId("group-ui")).toBeVisible();
    await expect(page.getByTestId("field-ui.theme")).toBeHidden();
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
    // The save bar only exists once something changes.
    await expect(page.getByTestId("settings-save")).toHaveCount(0);
    await page.getByTestId("ev-schedule-mon-enabled").check();
    await page.getByTestId("ev-schedule-mon-ready-by").fill("06:15");
    await page.getByTestId("ev-schedule-mon-min-pct").fill("90");
    const save = page.getByTestId("settings-save");
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

  test("the Car group's brand/model pickers are populated from /api/cars and autofill battery "
    + "kWh without touching the wallbox charger_kw", async ({ page }) => {
    const SCHEMA_CAR = [
      {
        key: "ev.car_id", label: "Car", type: "text", default: "",
        group: "ev", help: "Pick your car so capacity and AC limit are right.",
        min: null, max: null, options: null, step: null, unit: "", advanced: false, applies: "live",
      },
      {
        key: "ev.battery_kwh", label: "Battery capacity", type: "number", default: 57.5,
        group: "ev",
        help: "Usable battery capacity — autofilled from the car picker, override if you know "
          + "better.",
        min: 10, max: 150, options: null, step: 0.5, unit: "kWh", advanced: false, applies: "live",
      },
      {
        key: "ev.charger_kw", label: "Charger power", type: "number", default: 7.4,
        group: "ev", help: "The car charger's power — sets how long a charge takes.",
        min: 1, max: 22, options: null, step: 0.5, unit: "kW", advanced: false, applies: "live",
      },
    ];
    const CARS_RESP = {
      brands: ["Skoda", "Tesla"],
      cars: [
        {
          id: "skoda-enyaq-80", brand: "Skoda", model: "Enyaq 80",
          battery_net_kwh: 77, max_ac_kw: 11, years: "2021–present",
        },
        {
          id: "tesla-model-y-long-range", brand: "Tesla", model: "Model Y Long Range",
          battery_net_kwh: 75, max_ac_kw: 11, years: "2020–present",
        },
        {
          id: "tesla-model-y-rwd", brand: "Tesla", model: "Model Y RWD",
          battery_net_kwh: 57.5, max_ac_kw: 11, years: "2022–present",
        },
      ],
    };
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({
            schema: SCHEMA_CAR,
            values: { "ev.car_id": "", "ev.battery_kwh": 57.5, "ev.charger_kw": 7.4 },
          }),
        });
      } else {
        await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      }
    });
    await page.route("**/api/cars", async (route) => {
      await route.fulfill({
        status: 200, contentType: "application/json", body: JSON.stringify(CARS_RESP),
      });
    });
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-ev").click();

    const brandSelect = page.getByTestId("car-brand-select");
    const modelSelect = page.getByTestId("car-model-select");
    await expect(brandSelect).toBeVisible();
    await expect(modelSelect).toBeVisible();
    // Populated from the (mocked) /api/cars — brands sorted, models filtered by brand.
    await expect(brandSelect.locator("option")).toContainText(["Custom", "Skoda", "Tesla"]);

    await brandSelect.selectOption("Tesla");
    await expect(modelSelect.locator("option")).toContainText([
      "Model Y Long Range", "Model Y RWD",
    ]);
    await modelSelect.selectOption("tesla-model-y-long-range");

    // Autofills battery kWh from the picked model...
    await expect(page.locator("#set-ev\\.battery_kwh")).toHaveValue("75");
    // ...shows the selected car's specs inline...
    await expect(page.getByTestId("car-picker-specs")).toContainText("75 kWh usable");
    await expect(page.getByTestId("car-picker-specs")).toContainText("11 kW AC max");
    // ...and shows the car's AC max as a hint near charger_kw, WITHOUT overwriting it (still 7.4).
    await expect(page.locator("#set-ev\\.charger_kw")).toHaveValue("7.4");
    await expect(page.getByTestId("car-ac-hint")).toContainText("11 kW");

    // Picking "Custom" clears the car (battery kWh stays as the user's overridden value).
    await brandSelect.selectOption("");
    await expect(page.getByTestId("car-picker-specs")).toHaveCount(0);
    await expect(page.locator("#set-ev\\.battery_kwh")).toHaveValue("75");
  });

  test("enum selects show humanised labels while submitting the raw token", async ({ page }) => {
    const SCHEMA_ENUM = [
      {
        key: "prices.export_price_model", label: "Export (feed-in) value", type: "enum",
        default: "net_metering", group: "prices", help: "",
        min: null, max: null, options: ["net_metering", "spot_minus_tax", "fixed"],
        step: null, unit: "", advanced: false, applies: "live",
      },
    ];
    let saved: Record<string, unknown> = {};
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        saved = JSON.parse(route.request().postData() || "{}");
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ values: { "prices.export_price_model": saved["prices.export_price_model"] } }),
        });
      } else {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({
            schema: SCHEMA_ENUM, values: { "prices.export_price_model": "net_metering" },
          }),
        });
      }
    });
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-prices").click();
    const sel = page.locator("#set-prices\\.export_price_model");
    // No raw snake_case tokens on screen — options are humanised.
    await expect(sel.locator("option")).toContainText(["Net metering", "Spot minus tax", "Fixed"]);
    // The submitted VALUE stays the token.
    await sel.selectOption("spot_minus_tax");
    await page.getByTestId("settings-save").click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    expect(saved["prices.export_price_model"]).toBe("spot_minus_tax");
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
