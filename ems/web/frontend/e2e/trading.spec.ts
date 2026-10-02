// E-11 Trading portal (#trading): nav, status, settings smoke.
import { expect, test } from "@playwright/test";

import { mockRoute } from "./route-mock";

const TRADING_RESP = {
  trading_enabled: false,
  min_extra_eur: 0.5,
  max_export_kwh_per_day: 0,
  export_mode: "peak_slice",
  export_price_model: "spot_minus_tax",
  allow_export_discharge: false,
  probe_active: false,
  dry_run: true,
  operational: false,
  writes_allowed: false,
  block_reason: "yaml/control dry-run or mock/replay floor — no battery writes",
  evaluation: {
    enabled: false,
    would_trade: false,
    reason: "Geen handel: trading staat uit (alleen huis/zelfconsumptie).",
    t_eur: 0,
    z_eur: 0,
    extra_eur: 0,
    projection: true,
    projection_note:
      "T−Z is a projection of today's plan, not the act decision.",
    data_quality: "complete",
    validator: "valid",
  },
  copy: {
    house: "Pad Z = energie voor het huis (zelfconsumptie).",
    sell: "Pad T = goedkoop laden / duur verkopen aan het net.",
  },
};

test.describe("Trading view", () => {
  test("nav reaches #trading and shows status + arm controls", async ({ page }) => {
    const trading = await mockRoute(page, "**/api/trading", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(TRADING_RESP),
      }),
    );
    await page.goto("/#trading");
    await expect(page.getByTestId("trading-view")).toBeVisible();
    await expect(page.getByTestId("nav-trading")).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("trading-live-label")).toContainText("Writes blocked");
    await expect(page.getByTestId("trading-eval-reason")).toContainText("trading staat uit");
    await expect(page.getByTestId("trading-eval-projection")).toContainText("not the act decision");
    await expect(page.getByTestId("trading-eval-projection")).toContainText("Data quality: complete");
    await expect(page.getByTestId("trading-eval-projection")).toContainText("Plan validator: valid");
    await expect(page.getByTestId("trading-enabled")).toBeVisible();
    await expect(page.getByTestId("trading-arm")).toBeVisible();
    await expect(page.getByTestId("trading-test-blocked")).toBeVisible();
    trading.assertRequested();
  });

  test("arm confirm posts allow_export_discharge", async ({ page }) => {
    let posted: Record<string, unknown> | null = null;
    await mockRoute(page, "**/api/trading", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(TRADING_RESP),
      }),
    );
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      posted = JSON.parse(route.request().postData() || "{}");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ values: posted }),
      });
    });
    await page.goto("/#trading");
    await page.getByTestId("trading-arm").click();
    await expect(page.getByTestId("trading-arm-confirm")).toBeVisible();
    await page.getByTestId("trading-arm-confirm").click();
    await expect.poll(() => posted?.["control.allow_export_discharge"]).toBe(true);
  });
});
