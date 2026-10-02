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
    await expect(page.getByTestId("trading-live-label")).toContainText("Dry-run");
    await expect(page.getByTestId("trading-eval-reason")).toContainText("trading staat uit");
    await expect(page.getByTestId("trading-enabled")).toBeVisible();
    await expect(page.getByTestId("trading-arm")).toBeVisible();
    await expect(page.getByTestId("trading-test-btn")).toBeVisible();
    trading.assertRequested();
  });
});
