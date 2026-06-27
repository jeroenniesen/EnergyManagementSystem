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

  test("Settings tab renders a grouped, schema-driven form", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    const s = page.getByTestId("settings");
    await expect(s).toBeVisible();
    await expect(s).toContainText("Planner economics");
    await expect(s).toContainText("Control & safety");
    await expect(page.getByTestId("field-ui.theme")).toBeVisible();
    // The dashboard panels must be hidden while the Settings view is active.
    await expect(page.getByTestId("status-grid")).toHaveCount(0);
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
    const save = page.getByTestId("settings-save");
    await expect(save).toBeDisabled(); // nothing changed yet
    await page.locator("#set-ui\\.theme").selectOption("dark");
    await expect(save).toBeEnabled();
    await save.click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    expect(saved["ui.theme"]).toBe("dark");
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
    await page.locator("#set-ui\\.theme").selectOption("dark");
    await page.getByTestId("settings-save").click();
    await expect(page.getByTestId("err-ui.theme")).toBeVisible();
  });
});
