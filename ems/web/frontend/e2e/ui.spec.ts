import { expect, test } from "@playwright/test";

test.describe("EMS dashboard", () => {
  test("renders the status dashboard with reconstructed load", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Smart Energy Manager" })).toBeVisible();

    // DRY-RUN badge must be visible (M0a is read-only).
    await expect(page.getByTestId("run-mode-badge")).toHaveText("DRY-RUN");

    // The status grid renders, including the reconstructed house-load value (1.00 kW).
    const grid = page.getByTestId("status-grid");
    await expect(grid).toBeVisible();
    await expect(grid).toContainText("House load");
    await expect(grid).toContainText("1.00 kW");
    await expect(grid).toContainText("55 %");
  });

  test("no API error banner when backend is up", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("status-grid")).toBeVisible();
    await expect(page.getByTestId("error")).toHaveCount(0);
  });

  test("shows the electricity price curve with a current price", async ({ page }) => {
    await page.goto("/");
    const prices = page.getByTestId("prices");
    await expect(prices).toBeVisible();
    await expect(prices).toContainText("Electricity price");
    await expect(page.getByTestId("price-now")).toContainText("/ kWh");
  });

  test("shows the plan timeline with a current intent", async ({ page }) => {
    await page.goto("/");
    const plan = page.getByTestId("plan");
    await expect(plan).toBeVisible();
    await expect(plan).toContainText("Plan — next 24h");
    // current intent is one of the human labels
    await expect(page.getByTestId("current-intent")).not.toHaveText("—");
  });

  test("shows the solar forecast with today's kWh", async ({ page }) => {
    await page.goto("/");
    const fc = page.getByTestId("forecast");
    await expect(fc).toBeVisible();
    await expect(fc).toContainText("Solar forecast");
    await expect(page.getByTestId("forecast-today")).toContainText("kWh today");
  });

  test("shows per-signal freshness chips", async ({ page }) => {
    await page.goto("/");
    const fr = page.getByTestId("freshness");
    await expect(fr).toBeVisible();
    await expect(fr).toContainText("grid: fresh");
  });

  test("shows the error banner when the status API returns 500", async ({ page }) => {
    await page.route("**/api/status", (route) =>
      route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' }),
    );
    await page.goto("/");
    await expect(page.getByTestId("error")).toBeVisible();
    await expect(page.getByTestId("status-grid")).toHaveCount(0);
  });
});
