import { expect, test } from "@playwright/test";

test.describe("EMS dashboard", () => {
  test("the whole dashboard explains itself (all panels render together)", async ({ page }) => {
    // GOAL §2 litmus: a first-time viewer sees status, price, forecast, plan, controller
    // decision, freshness and data-quality all on one screen, with no error banner.
    await page.goto("/");
    for (const id of [
      "run-mode-badge",
      "data-quality",
      "status-grid",
      "decision",
      "plan",
      "prices",
      "forecast",
      "freshness",
      "alerts",
    ]) {
      await expect(page.getByTestId(id), `panel ${id} should render`).toBeVisible();
    }
    await expect(page.getByTestId("error")).toHaveCount(0);
  });

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
    await expect(grid).toContainText("Battery mode");
    await expect(grid).toContainText("auto");
    await expect(grid).toContainText("Est. savings today");
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

  test("shows a data-quality badge and the dry-run alert", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("data-quality")).toBeVisible();
    await expect(page.getByTestId("alerts")).toContainText("Dry-run");
  });

  test("shows the controller decision (dry-run) panel", async ({ page }) => {
    await page.goto("/");
    const dec = page.getByTestId("decision");
    await expect(dec).toBeVisible();
    await expect(dec).toContainText("dry-run");
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

  test("shows tonight's charge target with an explanation", async ({ page }) => {
    await page.goto("/");
    const cn = page.getByTestId("charge-need");
    await expect(cn).toBeVisible();
    await expect(cn).toContainText("Tonight's charge target");
    // MockSource SoC 55% vs default target ~84% -> a non-empty, explanatory reason.
    await expect(page.getByTestId("charge-need-reason")).not.toHaveText("");
    await expect(page.getByTestId("charge-need-status")).toBeVisible();
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
