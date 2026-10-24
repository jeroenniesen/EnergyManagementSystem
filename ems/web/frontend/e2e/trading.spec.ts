// E-11 Trading portal (#trading): nav, status, settings smoke.
import { expect, test } from "@playwright/test";

import { mockRoute } from "./route-mock";

const TRADING_STEPS = [
  {
    id: "advice",
    ok: false,
    label: "Stap 1 — Trading aan (plan-only)",
    missing: "trading staat uit",
  },
  {
    id: "dry_run",
    ok: false,
    label: "Stap 2 — Dry-run T-vs-Z bekeken",
    missing: "niet vastgelegd",
  },
  {
    id: "arm",
    ok: false,
    label: "Stap 3 — Live export bewapend",
    missing: "nog niet bewapend",
  },
];

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
  dry_run_reviewed_at: null,
  arm_allowed: false,
  arm_block_reason: "yaml/control dry-run or mock/replay floor — no battery writes",
  steps: TRADING_STEPS,
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
    await expect(page.getByTestId("trading-step-dry_run")).toContainText("✗");
    await expect(page.getByTestId("trading-arm-step2-missing")).toContainText(
      "Ontbreekt: stap 2 — bekijk de dry-run",
    );
    await expect(page.getByTestId("trading-test-consequence")).toContainText("reservevloer");
    const startBtn = page.getByTestId("trading-test-btn");
    await expect(startBtn).toBeDisabled();
    await expect(startBtn).toHaveClass(/btn-danger/);
    await expect(page.getByTestId("trading-test-blocked")).toBeVisible();
    await expect(page.getByTestId("trading-test-blocked")).toContainText(
      TRADING_RESP.block_reason,
    );
    trading.assertRequested();
  });

  test("arm confirm posts allow_export_discharge", async ({ page }) => {
    let posted: Record<string, unknown> | null = null;
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
          dry_run_reviewed_at: "2026-03-15T10:00:00+00:00",
          arm_allowed: true,
          arm_block_reason: null,
          steps: TRADING_STEPS.map((s) =>
            s.id === "dry_run" ? { ...s, ok: true, missing: null } : s,
          ),
        }),
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
    await expect(page.getByTestId("trading-step-dry_run")).toContainText("✓");
    await page.getByTestId("trading-arm").click();
    await expect(page.getByTestId("trading-arm-confirm")).toBeVisible();
    await page.getByTestId("trading-arm-confirm").click();
    await expect.poll(() => posted?.["control.allow_export_discharge"]).toBe(true);
  });

  test("dry-run reviewed button posts to the reviewed endpoint", async ({ page }) => {
    let reviewed = false;
    await mockRoute(page, "**/api/trading", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(TRADING_RESP),
      }),
    );
    await page.route("**/api/trading/dry-run-reviewed", async (route) => {
      reviewed = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          reviewed_at: "2026-03-15T10:00:00+00:00",
        }),
      });
    });
    await page.goto("/#trading");
    await page.getByTestId("trading-dry-run-reviewed").click();
    await expect.poll(() => reviewed).toBe(true);
  });

  test("arm disabled when writes blocked (Watch-only / dry-run)", async ({ page }) => {
    await mockRoute(page, "**/api/trading", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...TRADING_RESP,
          dry_run_reviewed_at: "2026-03-15T10:00:00+00:00",
          steps: TRADING_STEPS.map((s) =>
            s.id === "dry_run" ? { ...s, ok: true, missing: null } : s,
          ),
        }),
      }),
    );
    await page.goto("/#trading");
    await expect(page.getByTestId("trading-arm")).toBeDisabled();
    await expect(page.getByTestId("trading-arm-block-reason")).toContainText("dry-run");
  });

  test("disarm stays enabled when armed", async ({ page }) => {
    await mockRoute(page, "**/api/trading", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...TRADING_RESP,
          allow_export_discharge: true,
          writes_allowed: false,
          steps: TRADING_STEPS.map((s) =>
            s.id === "arm" ? { ...s, ok: true, missing: null } : s,
          ),
        }),
      }),
    );
    await page.goto("/#trading");
    await expect(page.getByTestId("trading-disarm")).toBeEnabled();
  });

  test("TRD-02: writes allowed opens confirm, focus moves, one POST on confirm", async ({
    page,
  }) => {
    let testPosts = 0;
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
          test_battery_allowed: true,
        }),
      }),
    );
    await page.route("**/api/trading/test-battery", async (route) => {
      if (route.request().method() === "POST") {
        testPosts += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ok: true, detail: "Test gestart" }),
        });
      } else {
        await route.fallback();
      }
    });
    await page.goto("/#trading");
    await expect(page.getByTestId("trading-test-consequence")).toBeVisible();
    const startBtn = page.getByTestId("trading-test-btn");
    await expect(startBtn).toBeEnabled();
    await expect(startBtn).toHaveClass(/btn-danger/);
    await startBtn.click();
    const panel = page.getByTestId("trading-test-confirm-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toBeFocused();
    const confirmBtn = page.getByTestId("trading-test-confirm");
    await expect(confirmBtn).toHaveClass(/btn-danger/);
    await confirmBtn.click();
    await expect.poll(() => testPosts).toBe(1);
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
});
