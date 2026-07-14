import { expect, test } from "@playwright/test";

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
const BASE: Record<string, number | string> = { "planner.charge_slots": 12, "ui.theme": "auto" };

test.describe("EMS theme", () => {
  // Force the emulated OS preference so "auto" resolves deterministically.
  test.use({ colorScheme: "dark" });

  test.beforeEach(async ({ page }) => {
    let theme = "auto";
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        const body = JSON.parse(route.request().postData() || "{}");
        if (typeof body["ui.theme"] === "string") theme = body["ui.theme"];
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ values: { ...BASE, "ui.theme": theme } }),
        });
      } else {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ schema: SCHEMA, values: { ...BASE, "ui.theme": theme } }),
        });
      }
    });
  });

  test("auto theme follows the OS dark preference on load", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });

  test("choosing light applies the light theme immediately", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-manage").click();
    await page.getByTestId("group-ui").click();
    await page.locator("#set-ui\\.theme").selectOption("light");
    await page.getByTestId("settings-save").click();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  });
});
