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

  test("hides Test battery button while a probe is running", async ({ page }) => {
    await mockRoute(page, "**/api/trading", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...TRADING_RESP,
          dry_run: false,
          operational: true,
          writes_allowed: true,
          block_reason: null,
          probe_active: true,
        }),
      }),
    );
    await page.goto("/#trading");
    await expect(page.getByTestId("trading-test-running")).toBeVisible();
    await expect(page.getByTestId("trading-test-btn")).toHaveCount(0);
    await expect(page.getByTestId("trading-test-confirm")).toHaveCount(0);
  });

  test("#366 empty threshold and cap restore the value and post nothing", async ({ page }) => {
    let posts = 0;
    await mockRoute(page, "**/api/trading", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...TRADING_RESP,
          min_extra_eur: 0.5,
          max_export_kwh_per_day: 12,
        }),
      }),
    );
    await mockRoute(page, "**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        posts += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ values: {} }),
        });
        return;
      }
      await route.fallback();
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/#trading");
    const threshold = page.getByTestId("trading-threshold");
    const cap = page.getByTestId("trading-cap");
    await expect(threshold).toHaveValue("0.5");
    const noPost = page.waitForRequest(
      (req) => req.method() === "POST" && req.url().includes("/api/settings"),
      { timeout: 1000 },
    );
    await threshold.fill("");
    await threshold.blur();
    await expect(threshold).toHaveValue("0.5");
    await cap.fill("");
    await cap.blur();
    await expect(cap).toHaveValue("12");
    await expect(noPost).rejects.toThrow();
    expect(posts).toBe(0);
    await expect(page.getByTestId("trading-threshold-unit")).toHaveText("€/day");
    await expect(page.getByTestId("trading-cap-unit")).toHaveText("kWh");
    await expect(page.getByTestId("trading-cap-hint")).toContainText("0 = onbeperkt");
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      await expect(page.locator("html")).toHaveAttribute("data-theme", scheme);
      const unit = page.getByTestId("trading-cap-unit");
      await expect(unit).toBeVisible();
      const box = await unit.boundingBox();
      expect(box).toBeTruthy();
      expect(box!.x + box!.width).toBeLessThanOrEqual(390);
    }
  });

  test("#366 wheel does not change threshold or cap", async ({ page }) => {
    await mockRoute(page, "**/api/trading", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...TRADING_RESP,
          min_extra_eur: 0.5,
          max_export_kwh_per_day: 12,
        }),
      }),
    );
    await page.goto("/#trading");
    const threshold = page.getByTestId("trading-threshold");
    await threshold.click();
    await expect(threshold).toBeFocused();
    await threshold.evaluate((el) => {
      el.addEventListener("wheel", () => {
        el.dataset.wheeled = "yes";
      });
    });
    await threshold.hover();
    await page.mouse.wheel(0, 240);
    await expect(threshold).toHaveValue("0.5");
    await expect(threshold).toHaveAttribute("data-wheeled", "yes");
    const cap = page.getByTestId("trading-cap");
    await cap.click();
    await expect(cap).toBeFocused();
    await cap.evaluate((el) => {
      el.addEventListener("wheel", () => {
        el.dataset.wheeled = "yes";
      });
    });
    await cap.hover();
    await page.mouse.wheel(0, 240);
    await expect(cap).toHaveValue("12");
    await expect(cap).toHaveAttribute("data-wheeled", "yes");
  });

  test("#366 a valid threshold still posts on blur", async ({ page }) => {
    let minExtra = 0.5;
    let posts = 0;
    let last: Record<string, unknown> | null = null;
    await mockRoute(page, "**/api/trading", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...TRADING_RESP,
          min_extra_eur: minExtra,
          max_export_kwh_per_day: 12,
        }),
      }),
    );
    await mockRoute(page, "**/api/settings", async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      posts += 1;
      last = JSON.parse(route.request().postData() || "{}") as Record<string, unknown>;
      if (typeof last["planner.trading_min_extra_eur"] === "number") {
        minExtra = last["planner.trading_min_extra_eur"];
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ values: last }),
      });
    });
    await page.goto("/#trading");
    const threshold = page.getByTestId("trading-threshold");
    await threshold.fill("1.25");
    await threshold.blur();
    await expect.poll(() => posts).toBe(1);
    expect(last?.["planner.trading_min_extra_eur"]).toBe(1.25);
    await expect(threshold).toHaveValue("1.25");
  });
});
