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

  // auth slice 3 web: the "app" project is authenticated with a migrated shared-token ACCESS
  // token (see auth.setup.ts / e2e-auth.ts) — kind "access", not "session". The API tokens panel
  // must render its quiet sign-in hint here, never the manage UI (design §5: the whole
  // /api/auth/tokens* surface is interactive-session-only) — this is exactly the scenario the
  // requirement calls out: a real access-token caller must see the hint, not a 403-driven mess.
  test("account tokens panel shows the sign-in hint (not the manage UI) for an access-token caller", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    const panel = page.getByTestId("account-tokens");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("account-tokens-hint")).toBeVisible();
    await expect(page.getByTestId("account-tokens-list")).toHaveCount(0);
    await expect(page.getByLabel("Name", { exact: true })).toHaveCount(0);
  });

  test("the solar-confidence planner setting renders as a drag slider", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    // Sidebar → open the Planner section in the content pane (one section at a time now).
    await page.getByTestId("group-planner").click();
    const field = page.getByTestId("field-planner.solar_confidence");
    await expect(field).toBeVisible();
    // It's a drag slider (range input) with a live read-out, not a plain number box.
    await expect(field.locator("input[type=range]")).toBeVisible();
  });

  // feat/ux-batch-3 (CLAUDE.md honesty ask): a read-only info callout under solar_confidence,
  // never a fake toggle — scenario-based planning isn't live yet.
  test("a read-only scenario-intelligence callout sits under solar confidence (no fake toggle)", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    await page.getByTestId("group-planner").click();
    const hint = page.getByTestId("scenario-intelligence-hint");
    await expect(hint).toBeVisible();
    await expect(hint).toContainText("forecast dial the planner actually uses today");
    await expect(hint).toContainText("Scenario-based planning");
    await expect(hint).toContainText("pessimistic/expected/optimistic futures");
    // Informational only — no input/toggle inside the callout itself.
    await expect(hint.locator("input, button")).toHaveCount(0);
  });

  test("the sidebar groups sections; a section opens in the content pane on click", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
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
    await page.getByTestId("nav-manage").click();
    await page.getByTestId("group-ui").click();
    await page.locator("#set-ui\\.theme").selectOption("dark");
    await page.getByTestId("settings-save").click();
    await expect(page.getByTestId("err-ui.theme")).toBeVisible();
  });

  // B-60: advisor suggestions gain a one-tap "Apply" — still one confirm (the existing sticky
  // save bar), still reversible (Discard), still audit-logged server-side like any settings save.
  test.describe("advisor 'Apply' (B-60)", () => {
    const SCHEMA_SOLAR = [
      {
        key: "planner.solar_confidence", label: "Solar forecast confidence", type: "number",
        default: 80, group: "planner", help: "", min: 30, max: 100, options: null, step: 5,
        unit: "%", advanced: false, applies: "live", slider: true,
      },
    ];

    test("a differing suggestion shows an Apply button; applying it dirties the field (same "
      + "set() as the slider) and Save posts the suggested value", async ({ page }) => {
      let saved: Record<string, unknown> = {};
      await page.route("**/api/settings", async (route) => {
        if (route.request().method() === "POST") {
          saved = JSON.parse(route.request().postData() || "{}");
          await route.fulfill({
            status: 200, contentType: "application/json",
            body: JSON.stringify({
              values: { "planner.solar_confidence": saved["planner.solar_confidence"] },
            }),
          });
        } else {
          await route.fulfill({
            status: 200, contentType: "application/json",
            body: JSON.stringify({
              schema: SCHEMA_SOLAR, values: { "planner.solar_confidence": 80 },
            }),
          });
        }
      });
      await page.route("**/api/advisor/solar-confidence", async (route) => {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({
            advice: {
              recommended_pct: 65, n_slots: 96, median_ratio_pct: 78.4, p25_ratio_pct: 63.2,
              current_pct: 80, delta_pct: -15,
            },
          }),
        });
      });
      await page.goto("/");
      await page.getByTestId("nav-manage").click();
      await page.getByTestId("group-planner").click();

      await expect(page.getByTestId("advisor-solar-confidence")).toBeVisible();
      const applyBtn = page.getByRole("button", {
        name: "Apply suggested solar confidence 65 percent",
      });
      await expect(applyBtn).toBeVisible();
      await expect(applyBtn).toHaveText("Apply 65%");
      await expect(page.getByTestId("settings-savebar")).toHaveCount(0);

      await applyBtn.click();

      // The field's own value updates — it's the SAME set() the slider control uses.
      const field = page.getByTestId("field-planner.solar_confidence");
      await expect(field).toContainText("65");
      // The button becomes a muted "applied — save to confirm" state.
      await expect(page.getByTestId("advisor-solar-confidence-applied")).toBeVisible();
      await expect(page.getByTestId("advisor-solar-confidence-apply")).toHaveCount(0);
      // Nothing bypasses the normal path: the existing sticky save bar is the one confirm.
      const bar = page.getByTestId("settings-savebar");
      await expect(bar).toBeVisible();
      await expect(bar).toContainText("1 unsaved change");

      await page.getByTestId("settings-save").click();
      await expect(page.getByTestId("settings-saved")).toBeVisible();
      expect(saved["planner.solar_confidence"]).toBe(65);
    });

    test("editing the field manually after Apply returns the hint to normal", async ({ page }) => {
      await page.route("**/api/settings", async (route) => {
        if (route.request().method() === "GET") {
          await route.fulfill({
            status: 200, contentType: "application/json",
            body: JSON.stringify({
              schema: SCHEMA_SOLAR, values: { "planner.solar_confidence": 80 },
            }),
          });
        } else {
          await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
        }
      });
      await page.route("**/api/advisor/solar-confidence", async (route) => {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({
            advice: {
              recommended_pct: 65, n_slots: 96, median_ratio_pct: 78.4, p25_ratio_pct: 63.2,
              current_pct: 80, delta_pct: -15,
            },
          }),
        });
      });
      await page.goto("/");
      await page.getByTestId("nav-manage").click();
      await page.getByTestId("group-planner").click();

      await page.getByRole("button", { name: "Apply suggested solar confidence 65 percent" })
        .click();
      await expect(page.getByTestId("advisor-solar-confidence-applied")).toBeVisible();

      // A manual edit (arrow-key nudge on the slider) reverts the hint to its normal state.
      const slider = page.locator("#set-planner\\.solar_confidence");
      await slider.focus();
      await slider.press("ArrowLeft");
      await expect(slider).not.toHaveValue("65");

      await expect(page.getByTestId("advisor-solar-confidence-applied")).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: "Apply suggested solar confidence 65 percent" }),
      ).toBeVisible();
    });

    test("a suggestion matching the current value shows a check note and no Apply button", async ({
      page,
    }) => {
      await page.route("**/api/settings", async (route) => {
        if (route.request().method() === "GET") {
          await route.fulfill({
            status: 200, contentType: "application/json",
            body: JSON.stringify({
              schema: SCHEMA_SOLAR, values: { "planner.solar_confidence": 65 },
            }),
          });
        } else {
          await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
        }
      });
      await page.route("**/api/advisor/solar-confidence", async (route) => {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({
            advice: {
              recommended_pct: 65, n_slots: 96, median_ratio_pct: 78.4, p25_ratio_pct: 63.2,
              current_pct: 65, delta_pct: 0,
            },
          }),
        });
      });
      await page.goto("/");
      await page.getByTestId("nav-manage").click();
      await page.getByTestId("group-planner").click();

      const match = page.getByTestId("advisor-solar-confidence-match");
      await expect(match).toBeVisible();
      await expect(match).toContainText("matches your setting");
      await expect(page.getByTestId("advisor-solar-confidence-apply")).toHaveCount(0);
      await expect(page.getByTestId("settings-savebar")).toHaveCount(0);
    });
  });
});
