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
      "soc-forecast",
      "decision",
      "plan-detail",
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

  test("shows the aligned next-24h plan tile with a summary and legend", async ({ page }) => {
    await page.goto("/");
    const plan = page.getByTestId("plan-detail");
    await expect(plan).toBeVisible();
    await expect(plan).toContainText("Next 24 hours");
    // Plain-English summary of what the algorithm will do.
    await expect(page.getByTestId("plan-summary")).not.toHaveText("");
    // The legend explains the action colours.
    await expect(page.getByTestId("plan-legend")).toContainText("Charge");
    await expect(page.getByTestId("plan-legend")).toContainText("Discharge");
  });

  test("shows the SoC history+forecast chart with a narrative and legend", async ({ page }) => {
    await page.goto("/");
    const soc = page.getByTestId("soc-forecast");
    await expect(soc).toBeVisible();
    await expect(page.getByTestId("soc-svg")).toBeVisible();
    await expect(page.getByTestId("soc-predicted")).toBeVisible(); // the dashed prediction line
    // A horizontal <line> has a zero-height box, so assert it rendered rather than "visible".
    await expect(page.getByTestId("soc-reserve-line")).toBeAttached();
    await expect(page.getByTestId("soc-narrative")).not.toHaveText("");
    await expect(page.getByTestId("soc-legend")).toContainText("Recorded");
    await expect(page.getByTestId("soc-legend")).toContainText("Predicted");
  });

  test("shows a per-tower breakdown for a multi-battery cluster", async ({ page }) => {
    // Live-only data — route-mock /api/battery to a two-tower cluster.
    await page.route("**/api/battery", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          current_mode: null,
          capabilities: null,
          aggregate: {
            soc_pct: 49.5, power_w: -490, capacity_kwh: 10.98,
            online_towers: 2, total_towers: 2,
          },
          towers: [
            { ip: "192.168.50.53", role: "master", soc_pct: 50, power_w: -250,
              capacity_kwh: 5.38, online: true },
            { ip: "192.168.50.22", role: "slave", soc_pct: 49, power_w: -240,
              capacity_kwh: 5.6, online: true },
          ],
        }),
      }),
    );
    await page.goto("/");
    await expect(page.getByTestId("tower-chip-aggregate")).toContainText("cluster avg");
    await expect(page.getByTestId("tower-chip")).toHaveCount(2);
    await expect(page.getByTestId("tower-chips")).toContainText("master");
    await expect(page.getByTestId("tower-chips")).toContainText("slave");
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

  test("System tab shows the readiness checks", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-system").click();
    await expect(page.getByTestId("system")).toBeVisible();
    await expect(page.getByTestId("checks")).toBeVisible();
    // Fully wired mock backend -> history store reachable, battery probed, writes open.
    await expect(page.getByTestId("check-history_store")).toContainText("reachable");
    await expect(page.getByTestId("check-battery")).toBeVisible();
    await expect(page.getByTestId("check-auth")).toContainText("open");
    // Per-signal live sensor checks (the "senses"): mock backend reports all signals fresh.
    await expect(page.getByTestId("check-sensor.grid")).toContainText("fresh");
    await expect(page.getByTestId("system-overall")).toBeVisible();
    // Export links present with the right download hrefs.
    await expect(page.getByTestId("export-raw")).toHaveAttribute(
      "href",
      "/api/export?kind=raw&format=csv",
    );
    await expect(page.getByTestId("export-derived")).toBeVisible();
    // Dashboard panels hidden while on the System view.
    await expect(page.getByTestId("status-grid")).toHaveCount(0);
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
