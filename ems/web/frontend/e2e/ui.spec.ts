import { expect, type Page, test } from "@playwright/test";

// The detailed panels (power tiles, Sankey, charge target, controller decision, AI note, data
// status) now live in a collapsed "Advanced" section — open it before asserting on them.
async function openAdvanced(page: Page) {
  await page.getByTestId("advanced-toggle").click();
  await expect(page.getByTestId("advanced-body")).toBeVisible();
}

// The energy story (past/next toggle + tiles + charts) now lives in the "See the full plan"
// disclosure, collapsed by default — open it before asserting on the story.
async function openPlan(page: Page) {
  await page.getByTestId("plan-disclosure-toggle").click();
  await expect(page.getByTestId("plan-disclosure-body")).toBeVisible();
}

// B-68: a minimal-but-complete /api/battery-plan payload, so a test can mock just the
// `confidence` block without hand-building the rest of the (large) contract every time.
function batteryPlanFixture(confidence: { level: string; reasons: string[] }) {
  const now = new Date();
  return {
    status: "on_track",
    summary: "Next 24h — plan is on track.",
    current_action: "self_consume",
    current_reason: "Battery is following the current plan.",
    window_start: now.toISOString(),
    window_end: new Date(now.getTime() + 24 * 3600e3).toISOString(),
    current_soc_pct: 60,
    reserve_soc_pct: 10,
    target_soc_pct: 88,
    target_deadline: null,
    planned_grid_topup_kwh: 0,
    deviation: { status: "ok", message: "On track." },
    warnings: [],
    graph: {
      forecast_soc: [], actual_soc: [], reserve_line: [], target_line: [],
      planned_actions: [], price_windows: [], solar: [],
    },
    confidence,
  };
}

test.describe("EMS dashboard", () => {
  test("the calm home surfaces the essentials, with detail behind disclosures", async ({ page }) => {
    // A first-time viewer sees the hero (one verdict), the score pills, the story card, the
    // strategy — the full plan and the technical detail are each one tap deeper. No error banner.
    await page.goto("/");
    for (const id of [
      "run-mode-badge",
      "data-quality",
      "home-state",
      "home-scores",
      "battery-plan",
      "plan-disclosure",
      "strategy-card",
      "advanced",
      "alerts",
    ]) {
      await expect(page.getByTestId(id), `panel ${id} should render`).toBeVisible();
    }
    // The full plan (the story with its tiles + charts) is collapsed by default — not shouting.
    await expect(page.getByTestId("energy-story")).toHaveCount(0);
    // The technical detail is present but tucked behind Advanced.
    await expect(page.getByTestId("decision")).toHaveCount(0);
    await expect(page.getByTestId("freshness")).toHaveCount(0);
    await openAdvanced(page);
    await expect(page.getByTestId("decision")).toBeVisible();
    await expect(page.getByTestId("freshness")).toBeVisible();
    await expect(page.getByTestId("error")).toHaveCount(0);
  });

  test("a time-of-day sky backdrop renders behind the app", async ({ page }) => {
    await page.goto("/");
    const sky = page.getByTestId("sky");
    await expect(sky).toBeAttached();
    await expect(sky).toHaveAttribute("data-phase", /night|dawn|day|dusk/);
  });

  test("the sky shows the daytime landscape scene during the day", async ({ page }) => {
    // Mock a daytime window (sunrise 4h ago, sunset in 4h) → the day phase + its illustrated scene.
    await page.route("**/api/sky", (route) => {
      const now = Date.now();
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          now: new Date(now).toISOString(),
          sunrise: new Date(now - 4 * 3600e3).toISOString(),
          sunset: new Date(now + 4 * 3600e3).toISOString(),
        }),
      });
    });
    await page.goto("/");
    const sky = page.getByTestId("sky");
    await expect(sky).toHaveAttribute("data-phase", "day");
    // The landscape is an illustrated image (a background-image), not a flat gradient.
    await expect(sky).toHaveCSS("background-image", /url\(.*day.*\.webp.*\)/);
  });

  test("the hero synthesises one verdict + a calm 'nothing needed' act line", async ({ page }) => {
    await page.goto("/");
    const hero = page.getByTestId("home-state");
    await expect(hero).toBeVisible();
    await expect(hero).toHaveAttribute("data-tone", /good|watching|controlling|attention/);
    // The verdict headline (the old status headline, absorbed into the hero).
    await expect(page.getByTestId("hero-verdict")).toContainText("Watching");
    // One synthesis line combining the on-track verdict + the day-score summary (reused strings).
    const synth = page.getByTestId("hero-synthesis");
    await expect(synth).toContainText("On track");
    await expect(synth).toContainText("brilliant day");
    await expect(synth).toContainText("·"); // the two strings are joined into one line
    // The explicit answer to "do I need to act?" — calm, because nothing needs attention.
    await expect(page.getByTestId("hero-act")).toHaveText("Nothing needed from you.");
  });

  test("B-68: a high-confidence plan shows a calm chip with no reason sub-line", async ({ page }) => {
    await page.route("**/api/battery-plan", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify(batteryPlanFixture({
          level: "high",
          reasons: ["Fresh data, calibrated forecast, battery responding — nothing is holding this plan back."],
        })),
      }),
    );
    await page.goto("/");
    const chip = page.getByTestId("confidence-chip");
    await expect(chip).toBeVisible();
    await expect(chip).toHaveAttribute("data-level", "high");
    await expect(chip).toHaveText("High confidence");
    // Calm stays calm: high confidence needs no explanation beyond the chip.
    await expect(page.getByTestId("hero-confidence-reason")).toHaveCount(0);
  });

  test("B-68: a medium-confidence plan shows an amber chip + the leading reason", async ({ page }) => {
    await page.route("**/api/battery-plan", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify(batteryPlanFixture({
          level: "medium",
          reasons: ["Still learning your roof — under 2.0 days of forecast evidence so far."],
        })),
      }),
    );
    await page.goto("/");
    const chip = page.getByTestId("confidence-chip");
    await expect(chip).toHaveAttribute("data-level", "medium");
    await expect(chip).toHaveText("Medium confidence");
    const reason = page.getByTestId("hero-confidence-reason");
    await expect(reason).toBeVisible();
    await expect(reason).toContainText("Still learning your roof");
  });

  test("B-68: a low-confidence plan shows a red chip + the safety-fallback reason", async ({ page }) => {
    await page.route("**/api/battery-plan", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify(batteryPlanFixture({
          level: "low",
          reasons: [
            "Safety fallback active — EMS is holding, not planning.",
            "Some live data is stale, so the plan can't be trusted right now.",
          ],
        })),
      }),
    );
    await page.goto("/");
    const chip = page.getByTestId("confidence-chip");
    await expect(chip).toHaveAttribute("data-level", "low");
    await expect(chip).toHaveText("Low confidence");
    // The tooltip carries every reason, joined.
    await expect(chip).toHaveAttribute("title", /Safety fallback active.*Some live data is stale/);
    // Only the FIRST reason renders as the visible sub-line.
    const reason = page.getByTestId("hero-confidence-reason");
    await expect(reason).toHaveText("Safety fallback active — EMS is holding, not planning.");
  });

  test("renders the status dashboard with reconstructed load", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Smart Energy Manager" })).toBeVisible();

    // Run-mode badge in plain language (dry-run => "Watching only"; M0a is read-only).
    await expect(page.getByTestId("run-mode-badge")).toHaveText("Watching only");

    // The live snapshot now rides the story card's footer: savings, battery level and mode.
    const footer = page.getByTestId("story-footer");
    await expect(footer).toBeVisible();
    await expect(footer).toContainText("55%");
    await expect(footer).toContainText("Battery");
    await expect(footer).toContainText("auto");
    await expect(footer).toContainText("Saved today");
    // The reconstructed house-load value (1.00 kW) lives with the detail metrics behind Advanced.
    await openAdvanced(page);
    const detail = page.getByTestId("detail-grid");
    await expect(detail).toContainText("House load");
    await expect(detail).toContainText("1.00 kW");
  });

  // B-03b: "Saved today" now derives from /api/finance (measured), never the old plan-estimate tile
  // — and never a false "€0.00" before any price history exists.
  test("B-03b: the story footer shows the MEASURED saved-today figure from /api/finance", async ({
    page,
  }) => {
    await page.route("**/api/finance**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          period: "day", label: "today", partial: false, days: [],
          totals: { grid_cost_eur: 1.1, battery_cost_eur: 0.08, saved_eur: 2.34,
                    days_with_prices: 1, days_with_data: 1 },
        }),
      }),
    );
    await page.goto("/");
    const stat = page.getByTestId("saved-today");
    await expect(stat).toBeVisible();
    await expect(stat).toContainText("€2.34 measured");
  });

  test("B-03b: no price history yet shows 'measuring', never a false €0.00", async ({ page }) => {
    await page.route("**/api/finance**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          period: "day", label: "today", partial: true, days: [],
          totals: { grid_cost_eur: null, battery_cost_eur: null, saved_eur: null,
                    days_with_prices: 0, days_with_data: 0 },
        }),
      }),
    );
    await page.goto("/");
    const stat = page.getByTestId("saved-today");
    await expect(stat).toBeVisible();
    await expect(stat).toContainText("measuring");
    await expect(stat).not.toContainText("€0.00");
  });

  test("no API error banner when backend is up", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("battery-plan")).toBeVisible();
    await expect(page.getByTestId("error")).toHaveCount(0);
  });

  test("shows a data-quality badge and the watch-only alert", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("data-quality")).toBeVisible();
    await expect(page.getByTestId("alerts")).toContainText("Watch-only");
  });

  test("shows the controller decision (dry-run) panel", async ({ page }) => {
    await page.goto("/");
    await openAdvanced(page);
    const dec = page.getByTestId("decision");
    await expect(dec).toBeVisible();
    await expect(dec).toContainText("dry-run");
  });

  test("the story card is the single narrative; the plan holds the tracks + stats", async ({ page }) => {
    await page.goto("/");
    // The one narrative sentence lives on the story card (battery-plan) and is visible up front.
    await expect(page.getByTestId("battery-plan-summary")).toContainText("Next 24h");
    // The full plan (toggle + tiles + charts) is one tap deeper.
    await openPlan(page);
    const story = page.getByTestId("energy-story");
    await expect(story).toBeVisible();
    await expect(page.getByTestId("story-tag")).toContainText("the plan"); // Next is the default
    await expect(page.getByTestId("story-soc-line")).toBeAttached();
    await expect(page.getByTestId("story-target")).toBeAttached();
    await expect(page.getByTestId("story-reserve")).toBeAttached();
    await expect(page.getByTestId("story-stats")).toBeVisible();
    await expect(page.getByTestId("story-legend")).toBeVisible();
    // No duplicate narrative: the plan renders WITHOUT its own headline sentence.
    await expect(page.getByTestId("story-headline")).toHaveCount(0);
  });

  test("the full-plan disclosure is collapsed by default and opens on demand", async ({ page }) => {
    await page.goto("/");
    // Collapsed: the whole energy story (and its headline) is absent; the story card's narrative
    // is the only narrative sentence on the page.
    await expect(page.getByTestId("plan-disclosure-toggle")).toContainText("See the full plan");
    await expect(page.getByTestId("energy-story")).toHaveCount(0);
    await expect(page.getByTestId("story-headline")).toHaveCount(0);
    await expect(page.getByTestId("battery-plan-summary")).toBeVisible();
    // Open → the charts appear, still no duplicate narrative.
    await openPlan(page);
    await expect(page.getByTestId("energy-story")).toBeVisible();
    await expect(page.getByTestId("story-soc-line")).toBeAttached();
    await expect(page.getByTestId("story-headline")).toHaveCount(0);
    await expect(page.getByTestId("plan-disclosure-toggle")).toContainText("Hide the full plan");
  });

  test("toggling to Last 24h switches the story", async ({ page }) => {
    await page.goto("/");
    await openPlan(page);
    await page.getByTestId("story-past").click();
    await expect(page.getByTestId("story-tag")).toContainText("what happened");
    // Back to Next.
    await page.getByTestId("story-next").click();
    await expect(page.getByTestId("story-tag")).toContainText("the plan");
  });

  test("the next story shows an on-track verdict", async ({ page }) => {
    // The real (mock) backend always returns an on_track verdict for the next window.
    await page.goto("/");
    await openPlan(page);
    const verdict = page.getByTestId("on-track");
    await expect(verdict).toBeVisible();
    await expect(verdict).toHaveAttribute("data-status", /ahead|on_track|behind|unknown/);
  });

  test("the next story draws recent actuals before now + a behind verdict (mocked)", async ({
    page,
  }) => {
    // Build a next story with 3h of recorded actuals (rising SoC) then a plan, + a 'behind' verdict.
    const base = Date.parse("2026-06-29T09:00:00Z");
    const SLOT = 15 * 60 * 1000;
    const mk = (n: number, from: number, soc0: number, action: string) =>
      Array.from({ length: n }, (_, i) => ({
        start: new Date(from + i * SLOT).toISOString(),
        soc_pct: soc0 + i, grid_w: 100, solar_w: 800 + i * 50, battery_w: -200,
        load_w: 400, eur_per_kwh: 0.2, action,
      }));
    const recent = mk(12, base - 12 * SLOT, 40, "grid_charge"); // last 3h, actual (grid-fed charge)
    const slots = mk(20, base, 52, "self_consume"); // the plan
    const totals = {
      import_kwh: 1, export_kwh: 0, solar_kwh: 5, charge_kwh: 2, discharge_kwh: 1, load_kwh: 4,
      grid_cost_eur: 0.2, self_sufficiency_pct: 80, soc_start_pct: 40, soc_end_pct: 70,
      soc_min_pct: 40, soc_max_pct: 70,
    };
    await page.route("**/api/energy-story**", (route) => {
      if (!route.request().url().includes("window=past")) {
        return route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({
            window: "next", now: new Date(base).toISOString(), current_soc_pct: 52,
            reserve_soc_pct: 10, target_soc_pct: 88, target_kwh: 9, target_deadline: null,
            current_price_eur_per_kwh: 0.2, slots, totals, headline: "Next 24h — plan.",
            recent_hours: 3, recent,
            on_track: { status: "behind", actual_soc_pct: 52, target_soc_pct: 88,
              deficit_kwh: 7.7, message: "Behind — about 7.7 kWh short of the 88% target." },
            recent_review: { message: "Last 3h: 3.2 kWh solar (80% of the 4.0 kWh forecast); "
              + "battery +1.2/−0.3 kWh.", solar_actual_kwh: 3.2, solar_forecast_kwh: 4.0,
              solar_pct_of_forecast: 80 },
          }),
        });
      }
      return route.continue();
    });
    await page.goto("/");
    await openPlan(page);
    const verdict = page.getByTestId("on-track");
    await expect(verdict).toHaveAttribute("data-status", "behind");
    await expect(verdict).toContainText("Behind");
    // Both the measured (solid) and forecast (dashed) SoC lines render on the same chart.
    await expect(page.getByTestId("story-soc-actual")).toBeAttached();
    await expect(page.getByTestId("story-soc-line")).toBeAttached();
    // The "did we do right" review (solar vs forecast) is shown.
    await expect(page.getByTestId("recent-review")).toContainText("of the 4.0 kWh forecast");
  });

  // B-08: quiet, PAST-window-only "success marker" chips computed client-side from fields already
  // in the /api/energy-story payload — night = solar_w<5 (timezone-agnostic, no clock-hour guess),
  // grid-buy = per-slot action "grid_charge" (the same field BatteryPlan's chart legends "cheap
  // window"). Each renders ONLY when the payload can prove it; see EnergyStory.tsx for the exact
  // fields/thresholds.
  test("B-08: quiet success markers render only when the payload can honestly prove them", async ({
    page,
  }) => {
    const base = Date.parse("2026-06-29T00:00:00Z");
    const SLOT = 15 * 60 * 1000;
    // 3h of clean night: no solar, no grid import, the battery alone covering real load.
    const night = Array.from({ length: 12 }, (_, i) => ({
      start: new Date(base + i * SLOT).toISOString(),
      soc_pct: 70 - i * 0.5, grid_w: 0, solar_w: 0, battery_w: 500, load_w: 480,
      eur_per_kwh: 0.1, action: "discharge",
    }));
    // 1h of a deliberate, cheap grid-charge (the ONLY grid import in the window).
    const buy = Array.from({ length: 4 }, (_, i) => ({
      start: new Date(base + (12 + i) * SLOT).toISOString(),
      soc_pct: 64 + i, grid_w: 800, solar_w: 50, battery_w: -800, load_w: 300,
      eur_per_kwh: 0.05, action: "grid_charge",
    }));
    const slots = [...night, ...buy];
    const totals = {
      import_kwh: 0.8, export_kwh: 0, solar_kwh: 0.2, charge_kwh: 0.8, discharge_kwh: 2.4,
      load_kwh: 3.1, grid_cost_eur: 0.04, self_sufficiency_pct: 74,
      soc_start_pct: 70, soc_end_pct: 68, soc_min_pct: 62, soc_max_pct: 70,
    };
    await page.route("**/api/energy-story**", (route) => {
      if (route.request().url().includes("window=past")) {
        return route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({
            window: "past", now: new Date(base + 16 * SLOT).toISOString(), current_soc_pct: 68,
            reserve_soc_pct: 10, target_soc_pct: 88, target_kwh: 9, target_deadline: null,
            current_price_eur_per_kwh: 0.1, slots, totals,
            headline: "Last 24h — ran the night on the battery.",
          }),
        });
      }
      return route.continue();
    });
    await page.goto("/");
    await openPlan(page);
    await page.getByTestId("story-past").click();
    await expect(page.getByTestId("quiet-marker-night")).toContainText("ran the night on battery");
    await expect(page.getByTestId("quiet-marker-cheap")).toContainText(
      "bought only in the cheap window",
    );
  });

  test("B-08: withholds a marker the payload can't honestly support", async ({ page }) => {
    const base = Date.parse("2026-06-29T00:00:00Z");
    const SLOT = 15 * 60 * 1000;
    // Same night stretch, but one slot draws from the grid — the battery did NOT run the whole
    // night alone, so the "ran the night on battery" claim must be withheld.
    const night = Array.from({ length: 12 }, (_, i) => ({
      start: new Date(base + i * SLOT).toISOString(),
      soc_pct: 70 - i * 0.5, grid_w: i === 6 ? 300 : 0, solar_w: 0, battery_w: 500, load_w: 480,
      eur_per_kwh: 0.1, action: i === 6 ? "self_consume" : "discharge",
    }));
    // One import slot landed OUTSIDE a deliberate grid-charge — "bought only in the cheap window"
    // must also be withheld.
    const buy = Array.from({ length: 4 }, (_, i) => ({
      start: new Date(base + (12 + i) * SLOT).toISOString(),
      soc_pct: 64 + i, grid_w: 800, solar_w: 50, battery_w: -800, load_w: 300,
      eur_per_kwh: 0.05, action: i === 0 ? "self_consume" : "grid_charge",
    }));
    const slots = [...night, ...buy];
    const totals = {
      import_kwh: 1.0, export_kwh: 0, solar_kwh: 0.2, charge_kwh: 0.8, discharge_kwh: 2.4,
      load_kwh: 3.1, grid_cost_eur: 0.06, self_sufficiency_pct: 68,
      soc_start_pct: 70, soc_end_pct: 68, soc_min_pct: 62, soc_max_pct: 70,
    };
    await page.route("**/api/energy-story**", (route) => {
      if (route.request().url().includes("window=past")) {
        return route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({
            window: "past", now: new Date(base + 16 * SLOT).toISOString(), current_soc_pct: 68,
            reserve_soc_pct: 10, target_soc_pct: 88, target_kwh: 9, target_deadline: null,
            current_price_eur_per_kwh: 0.1, slots, totals, headline: "Last 24h.",
          }),
        });
      }
      return route.continue();
    });
    await page.goto("/");
    await openPlan(page);
    await page.getByTestId("story-past").click();
    await expect(page.getByTestId("story-soc")).toBeVisible(); // the past story did load
    await expect(page.getByTestId("quiet-marker-night")).toHaveCount(0);
    await expect(page.getByTestId("quiet-marker-cheap")).toHaveCount(0);
  });

  // B-31: the story could show "✓ No grid top-up needed" (server trust_markers) right beside
  // "⚠ Short of the target with no grid top-up planned" (the on-track caution) — the SAME fact
  // (no GRID_CHARGE_TO_TARGET slot in the plan) told as both comfort and warning. Single-voiced:
  // the comfort chip is suppressed once the verdict is "behind".
  test("B-31: suppresses the redundant comfort chip when the verdict is behind", async ({ page }) => {
    const base = Date.parse("2026-06-29T09:00:00Z");
    const SLOT = 15 * 60 * 1000;
    const slots = Array.from({ length: 20 }, (_, i) => ({
      start: new Date(base + i * SLOT).toISOString(),
      soc_pct: 60 + i * 0.2, grid_w: 50, solar_w: 600, battery_w: 0, load_w: 400,
      eur_per_kwh: 0.2, action: "self_consume",
    }));
    const totals = {
      import_kwh: 1.2, export_kwh: 0, solar_kwh: 6, charge_kwh: 0, discharge_kwh: 0, load_kwh: 4,
      grid_cost_eur: 0.24, self_sufficiency_pct: 70, soc_start_pct: 60, soc_end_pct: 64,
      soc_min_pct: 58, soc_max_pct: 66,
    };
    await page.route("**/api/energy-story**", (route) => {
      if (route.request().url().includes("window=past")) return route.continue();
      return route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          window: "next", now: new Date(base).toISOString(), current_soc_pct: 60,
          reserve_soc_pct: 10, target_soc_pct: 88, target_kwh: 9, target_deadline: null,
          current_price_eur_per_kwh: 0.2, slots, totals,
          headline: "Next 24h — running on solar + battery; no grid charging.",
          trust_markers: ["Reserve respected", "No grid top-up needed"],
          recent_hours: 3, recent: [],
          on_track: {
            status: "behind", actual_soc_pct: 60, target_soc_pct: 88, deficit_kwh: 6.2,
            message: "Short of the 88% target with no grid top-up planned — about 1.2 kWh will "
              + "come from the grid.",
          },
        }),
      });
    });
    await page.goto("/");
    // The hero's synthesis line (B-32) already, legitimately, mirrors this same on_track.message
    // as a quick-glance summary — that's a summary/detail sync, not the B-31 bug. The bug is the
    // comfort CHIP ("No grid top-up needed") appearing anywhere alongside it.
    await expect(page.getByTestId("hero-synthesis")).toContainText("no grid top-up planned");
    await openPlan(page);
    const verdict = page.getByTestId("on-track");
    await expect(verdict).toContainText("no grid top-up planned");
    const markers = page.getByTestId("trust-markers");
    await expect(markers).toContainText("Reserve respected");
    await expect(markers).not.toContainText("No grid top-up needed");
    // The comfort chip's exact copy never appears anywhere on the page once the verdict is behind.
    await expect(page.getByText("No grid top-up needed")).toHaveCount(0);
  });

  test("B-31: the comfort chip still shows when the plan really is on track (not over-suppressed)", async ({
    page,
  }) => {
    const base = Date.parse("2026-06-29T09:00:00Z");
    const SLOT = 15 * 60 * 1000;
    const slots = Array.from({ length: 20 }, (_, i) => ({
      start: new Date(base + i * SLOT).toISOString(),
      soc_pct: 60 + i, grid_w: 0, solar_w: 700, battery_w: -100, load_w: 300,
      eur_per_kwh: 0.2, action: "self_consume",
    }));
    const totals = {
      import_kwh: 0, export_kwh: 1, solar_kwh: 7, charge_kwh: 1, discharge_kwh: 0, load_kwh: 4,
      grid_cost_eur: 0, self_sufficiency_pct: 100, soc_start_pct: 60, soc_end_pct: 90,
      soc_min_pct: 60, soc_max_pct: 92,
    };
    await page.route("**/api/energy-story**", (route) => {
      if (route.request().url().includes("window=past")) return route.continue();
      return route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          window: "next", now: new Date(base).toISOString(), current_soc_pct: 60,
          reserve_soc_pct: 10, target_soc_pct: 88, target_kwh: 9, target_deadline: null,
          current_price_eur_per_kwh: 0.2, slots, totals,
          headline: "Next 24h — your solar fills the battery, then runs the evening on it.",
          trust_markers: ["Reserve respected", "No grid top-up needed", "On track for tonight's target"],
          recent_hours: 3, recent: [],
          on_track: {
            status: "ahead", actual_soc_pct: 60, target_soc_pct: 88, deficit_kwh: 0,
            message: "On track — projected to reach the 88% night target.",
          },
        }),
      });
    });
    await page.goto("/");
    await openPlan(page);
    const markers = page.getByTestId("trust-markers");
    await expect(markers).toContainText("No grid top-up needed");
  });

  test("shows the strategy card with a season picker and explanation", async ({ page }) => {
    await page.goto("/");
    const card = page.getByTestId("strategy-card");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("strategy-auto")).toBeVisible();
    await expect(page.getByTestId("strategy-summer")).toBeVisible();
    await expect(page.getByTestId("strategy-winter")).toBeVisible();
    await expect(page.getByTestId("strategy-summary")).not.toHaveText("");
    // Default is Auto -> the Auto option is selected and the resolved season is shown.
    await expect(page.getByTestId("strategy-auto")).toHaveAttribute("aria-checked", "true");
    await expect(page.getByTestId("strategy-active")).toContainText("Auto");
  });

  test("strategy card switches the running strategy", async ({ page }) => {
    let mode = "auto";
    await page.route("**/api/strategy", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          mode, active: mode === "winter" ? "winter" : "summer", auto: mode === "auto",
          summary: mode === "winter"
            ? "Arbitrage — charge in the cheapest hours and discharge the peaks."
            : "Solar-first — fill the battery from your panels.",
          grid_topup: true, max_topup_price: 0.3,
        }),
      }),
    );
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        mode = JSON.parse(route.request().postData() || "{}")["strategy.mode"] ?? mode;
        await route.fulfill({ status: 200, contentType: "application/json", body: "{\"values\":{}}" });
      } else {
        await route.continue();
      }
    });
    await page.goto("/");
    await expect(page.getByTestId("strategy-summary")).toContainText("Solar-first");
    await page.getByTestId("strategy-winter").click();
    await expect(page.getByTestId("strategy-winter")).toHaveAttribute("aria-checked", "true");
    await expect(page.getByTestId("strategy-summary")).toContainText("Arbitrage");
  });

  test("strategy card is operable with the keyboard (arrow keys)", async ({ page }) => {
    let mode = "auto";
    await page.route("**/api/strategy", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          mode, active: mode === "winter" ? "winter" : "summer", auto: mode === "auto",
          summary: "x", grid_topup: true, max_topup_price: 0.3,
        }),
      }),
    );
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        mode = JSON.parse(route.request().postData() || "{}")["strategy.mode"] ?? mode;
        await route.fulfill({ status: 200, contentType: "application/json", body: "{\"values\":{}}" });
      } else {
        await route.continue();
      }
    });
    await page.goto("/");
    await page.getByTestId("strategy-auto").focus();
    await page.keyboard.press("ArrowRight"); // Auto -> Summer
    await expect(page.getByTestId("strategy-summer")).toHaveAttribute("aria-checked", "true");
    await page.keyboard.press("ArrowRight"); // Summer -> Winter
    await expect(page.getByTestId("strategy-winter")).toHaveAttribute("aria-checked", "true");
  });

  test("summer shows an inline grid-top-up switch that toggles", async ({ page }) => {
    let topup = true;
    await page.route("**/api/strategy", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          mode: "summer", active: "summer", auto: false, summary: "Solar-first.",
          grid_topup: topup, max_topup_price: 0.3,
        }),
      }),
    );
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        const body = JSON.parse(route.request().postData() || "{}");
        if ("strategy.summer_grid_topup" in body) topup = body["strategy.summer_grid_topup"];
        await route.fulfill({ status: 200, contentType: "application/json", body: "{\"values\":{}}" });
      } else {
        await route.continue();
      }
    });
    await page.goto("/");
    const sw = page.getByTestId("strategy-grid-topup");
    await expect(sw).toBeVisible();
    await expect(sw).toHaveAttribute("aria-label", "Top up from the grid if the sun falls short");
    await expect(sw).toHaveAttribute("aria-checked", "true");
    await sw.click();
    await expect(sw).toHaveAttribute("aria-checked", "false");
  });

  test("the strategy card's Advanced link opens Settings", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("strategy-more").click();
    await expect(page.getByTestId("settings")).toBeVisible();
    await expect(page.getByTestId("settings")).toContainText("Strategy");
  });

  test("shows a 'car charging — battery held' badge when the car is charging", async ({ page }) => {
    await page.route("**/api/decision", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          intent: "hold_reserve", desired_mode: "idle", applied: false, outcome: "dry_run",
          reason: "dry-run: would set idle",
          plan_reason: "car charging — holding the battery so it won't discharge into the car",
          override_active: false, car_charging: true,
        }),
      }),
    );
    await page.goto("/");
    await openAdvanced(page);
    await expect(page.getByTestId("car-charging")).toContainText("Car charging");
    await expect(page.getByTestId("decision")).toContainText("won't discharge into the car");
  });

  test("the hold-battery-when-car-charging setting is in the panel", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-settings").click();
    await page.getByTestId("group-control").click();
    await expect(page.getByTestId("field-control.hold_battery_when_car_charging")).toBeVisible();
    await expect(
      page.getByTestId("field-control.hold_battery_when_car_charging"),
    ).toContainText("car");
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
            { ip: "192.0.2.53", role: "master", soc_pct: 50, power_w: -250,
              capacity_kwh: 5.38, online: true },
            { ip: "192.0.2.22", role: "slave", soc_pct: 49, power_w: -240,
              capacity_kwh: 5.6, online: true },
          ],
        }),
      }),
    );
    await page.goto("/");
    // The per-tower breakdown lives behind the battery tile now; it becomes clickable once the
    // cluster data loads (the hint switches to "see each battery").
    const tile = page.getByTestId("battery-tile");
    await expect(tile).toContainText("see each battery");
    await tile.click();
    await expect(page.getByTestId("battery-modal")).toBeVisible();
    await expect(page.getByTestId("tower-chip-aggregate")).toContainText("cluster avg");
    await expect(page.getByTestId("tower-chip")).toHaveCount(2);
    await expect(page.getByTestId("tower-chips")).toContainText("master");
    await expect(page.getByTestId("tower-chips")).toContainText("slave");
    // Escape closes the dialog.
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("battery-modal")).toHaveCount(0);
  });

  test("the Battery (power) tile opens a per-tower power breakdown", async ({ page }) => {
    // Same cluster data, but clicked from the POWER tile — the breakdown emphasises each tower's
    // power (with direction) instead of its SoC.
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
            { ip: "192.0.2.53", role: "master", soc_pct: 50, power_w: -250,
              capacity_kwh: 5.38, online: true },
            { ip: "192.0.2.22", role: "slave", soc_pct: 49, power_w: 600,
              capacity_kwh: 5.6, online: true },
          ],
        }),
      }),
    );
    await page.goto("/");
    // The power tile lives with the detail metrics behind Advanced.
    await openAdvanced(page);
    const tile = page.getByTestId("battery-power-tile");
    await expect(tile).toContainText("see each battery");
    await tile.click();
    const modal = page.getByTestId("battery-modal");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Battery power — per tower");
    // Per-tower power with direction: one charging ("in"), one discharging ("out").
    await expect(page.getByTestId("tower-chips")).toContainText("250 W in");
    await expect(page.getByTestId("tower-chips")).toContainText("600 W out");
    await page.keyboard.press("Escape");
    await expect(modal).toHaveCount(0);
  });

  test("the daily energy-distribution Sankey renders and the day can be changed", async ({
    page,
  }) => {
    const FLOWS = {
      date: "2026-06-28", has_data: true, partial: false,
      solar_to_home: 4.0, solar_to_car: 1.0, solar_to_battery: 3.0, solar_to_grid: 2.0,
      grid_to_home: 1.0, grid_to_car: 0.5, grid_to_battery: 0.5,
      battery_to_home: 2.5, battery_to_car: 0.0,
      solar_kwh: 10.0, grid_import_kwh: 2.0, grid_export_kwh: 2.0,
      battery_charge_kwh: 3.5, battery_discharge_kwh: 2.5, home_kwh: 7.5, car_kwh: 1.5,
      self_sufficiency_pct: 86.7,
    };
    await page.route("**/api/energy-distribution**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(FLOWS) }),
    );
    await page.goto("/");
    await openAdvanced(page);
    const card = page.getByTestId("energy-distribution");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("sankey")).toBeVisible();
    await expect(page.getByTestId("band-s-h")).toBeVisible(); // a solar→home band
    await expect(page.getByTestId("band-s-c")).toBeVisible(); // solar→car band (the new sink)
    await expect(page.getByTestId("dist-selfsuff")).toContainText("87%");
    // Day navigation: starts at Today (next disabled); stepping back enables it.
    await expect(page.getByTestId("dist-day")).toHaveText("Today");
    await expect(page.getByTestId("dist-next")).toBeDisabled();
    await page.getByTestId("dist-prev").click();
    await expect(page.getByTestId("dist-day")).toHaveText("Yesterday");
    await expect(page.getByTestId("dist-next")).toBeEnabled();
  });

  test("the energy-distribution card shows an empty state for a day with no data", async ({
    page,
  }) => {
    await page.route("**/api/energy-distribution**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ date: "2026-06-28", has_data: false, partial: false,
          solar_to_home: 0, solar_to_battery: 0, solar_to_grid: 0, grid_to_home: 0,
          grid_to_battery: 0, battery_to_home: 0, solar_kwh: 0, grid_import_kwh: 0,
          grid_export_kwh: 0, battery_charge_kwh: 0, battery_discharge_kwh: 0, home_kwh: 0,
          self_sufficiency_pct: null }),
      }),
    );
    await page.goto("/");
    await openAdvanced(page);
    await expect(page.getByTestId("dist-empty")).toBeVisible();
    await expect(page.getByTestId("sankey")).toHaveCount(0);
  });

  test("shows tonight's charge target with an explanation", async ({ page }) => {
    await page.goto("/");
    await openAdvanced(page);
    const cn = page.getByTestId("charge-need");
    await expect(cn).toBeVisible();
    await expect(cn).toContainText("Tonight's charge target");
    // MockSource SoC 55% vs default target ~84% -> a non-empty, explanatory reason.
    await expect(page.getByTestId("charge-need-reason")).not.toHaveText("");
    await expect(page.getByTestId("charge-need-status")).toBeVisible();
  });

  test("shows per-signal freshness chips", async ({ page }) => {
    await page.goto("/");
    await openAdvanced(page);
    const fr = page.getByTestId("freshness");
    await expect(fr).toBeVisible();
    await expect(fr).toContainText("Grid meter: up to date");
  });

  test("System tab shows the readiness checks", async ({ page }) => {
    await page.route("**/api/incidents", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          incidents: {
            total: 2, by_type: { cluster_mismatch: 1, command_failed: 1 },
            by_day: { "2026-06-28": 2 }, most_recent: "2026-06-28T18:00:00+00:00",
            last_7_days: 2,
          },
        }),
      }),
    );
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
    // Control-incident rollup (mocked): 2 incidents in the last 7 days, broken down by type.
    await expect(page.getByTestId("incidents")).toBeVisible();
    await expect(page.getByTestId("incidents")).toContainText("2 incidents in the last 7 days");
    await expect(page.getByTestId("incident-types")).toContainText("Cluster mismatch");
    await expect(page.getByTestId("incident-types")).toContainText("Command failed");
    // Export links present with the right download hrefs.
    await expect(page.getByTestId("export-package")).toHaveAttribute(
      "href",
      "/api/export/package",
    );
    await expect(page.getByTestId("export-raw")).toHaveAttribute(
      "href",
      "/api/export?kind=raw&format=csv",
    );
    await expect(page.getByTestId("export-derived")).toBeVisible();
    await expect(page.getByTestId("export-replay")).toHaveAttribute("href", "/api/replay");
    // Dashboard panels hidden while on the System view.
    await expect(page.getByTestId("battery-plan")).toHaveCount(0);
  });

  test("the Chat tab shows the assistant, off until AI is enabled", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-chat").click();
    await expect(page.getByTestId("chat")).toBeVisible();
    // The mock backend has AI off by default → the chat shows the enable-in-Settings hint.
    await expect(page.getByTestId("chat-disabled")).toBeVisible();
  });

  test("grounded FAQ answers work even with AI off", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-chat").click();
    await expect(page.getByTestId("faq")).toBeVisible();
    // Clicking a question reveals a deterministic answer (no AI needed).
    await page.getByTestId("faq-battery_safe").click();
    await expect(page.getByTestId("faq-answer-battery_safe")).toBeVisible();
  });

  test("System tab groups checks with a readiness sentence", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-system").click();
    await expect(page.getByTestId("system-readiness")).toBeVisible();
    await expect(page.getByTestId("check-group-Battery & control")).toBeVisible();
  });

  test("the chat answers a question when AI is enabled (mocked)", async ({ page }) => {
    await page.route("**/api/explainer", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ mode: "external_llm", active: true, language: "English" }),
      }),
    );
    await page.route("**/api/chat", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ answer: "Your battery is full and running the house.", source: "external_llm" }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-chat").click();
    await expect(page.getByTestId("chat-input")).toBeVisible();
    await page.getByTestId("chat-input").fill("why isn't it charging?");
    await page.getByTestId("chat-send").click();
    await expect(page.getByTestId("chat-log")).toContainText("running the house");
  });

  test("the Audit tab shows the change log", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("nav-audit").click();
    await expect(page.getByTestId("audit")).toBeVisible();
    await expect(page.getByTestId("audit")).toContainText("Audit log");
    await expect(page.getByTestId("audit-filter")).toBeVisible();
  });

  test("the audit log renders decision + config entries (mocked)", async ({ page }) => {
    await page.route("**/api/audit**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          entries: [
            { id: 2, ts: "2026-06-28T18:00:00+00:00", category: "battery_decision",
              summary: "Would set battery to charge — cheap window", detail: {} },
            { id: 1, ts: "2026-06-28T17:00:00+00:00", category: "config_change",
              summary: "Changed 1 setting(s): battery.min_reserve_soc", detail: {} },
          ],
        }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-audit").click();
    await expect(page.getByTestId("audit-list")).toContainText("Would set battery to charge");
    await expect(page.getByTestId("audit-list")).toContainText("Changed 1 setting");
  });

  test("the AI second-opinion card is hidden when AI is off", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("battery-plan")).toBeVisible();
    // Even inside Advanced, the card renders nothing while AI is off.
    await openAdvanced(page);
    await expect(page.getByTestId("ai-validation")).toHaveCount(0);
  });

  test("the AI second-opinion card shows a review when enabled (mocked)", async ({ page }) => {
    await page.route("**/api/ai/validation", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          latest: { text: "The plan looks sound — charging cheap and covering the peak.",
            ts: "2026-06-28T18:00:00+00:00", source: "external_llm" },
          active: true,
        }),
      }),
    );
    await page.goto("/");
    await openAdvanced(page);
    await expect(page.getByTestId("ai-validation-text")).toContainText("plan looks sound");
    await expect(page.getByTestId("ai-check")).toBeVisible();
  });

  test("the car card is absent when the EV feature is off", async ({ page }) => {
    await page.route("**/api/car/plan", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ enabled: false, plan: null, soc: null }),
      }),
    );
    await page.goto("/");
    await expect(page.getByTestId("battery-plan")).toBeVisible();
    await expect(page.getByTestId("car-card")).toHaveCount(0);
  });

  test("the car card asks for the car's charge level when there's no SoC anchor yet", async ({ page }) => {
    await page.route("**/api/car/plan", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ enabled: true, plan: null, soc: null, needs_anchor: true }),
      }),
    );
    await page.route("**/api/car/soc", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          soc: {
            soc_pct: 55, anchor_pct: 55, anchor_ts: new Date().toISOString(),
            added_kwh: 0, sessions_since_anchor: 0, age_hours: 0, stale: false,
          },
        }),
      }),
    );
    await page.goto("/");
    const card = page.getByTestId("car-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("What's the car's charge now?");
    await page.getByTestId("car-soc-input").fill("55");
    const [req] = await Promise.all([
      page.waitForRequest("**/api/car/soc"),
      page.getByTestId("car-soc-set").click(),
    ]);
    expect(JSON.parse(req.postData() || "{}")).toEqual({ pct: 55 });
  });

  test("the car card explains manual-only SoC when no EV meter is configured", async ({ page }) => {
    await page.route("**/api/car/plan", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          enabled: true,
          car_meter_configured: false,
          plan: null,
          soc: null,
          needs_anchor: true,
        }),
      }),
    );
    await page.goto("/");
    await expect(page.getByTestId("car-meter-missing")).toContainText("No EV meter");
    await expect(page.getByTestId("car-meter-missing")).toContainText("after driving or charging");
  });

  test("the car card explains manual-only SoC in the needs-schedule state too", async ({ page }) => {
    // Parity: the no-EV-meter warning must also show when a schedule is missing (not only in the
    // needs-anchor state), matching the iOS card which shows it in every enabled state.
    await page.route("**/api/car/plan", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          enabled: true,
          car_meter_configured: false,
          plan: null,
          soc: { pct: 55, source: "manual" },
          needs_schedule: true,
        }),
      }),
    );
    await page.goto("/");
    await expect(page.getByTestId("car-meter-missing")).toContainText("No EV meter");
    await expect(page.getByTestId("car-schedule-link")).toBeVisible();
  });

  test("the car card shows the full plan (SoC, advice, windows, timeline)", async ({ page }) => {
    // Slot/deadline times are anchored to "now" (floored to the 15-min grid, matching the
    // card's own timeline math) so the mocked plan lands inside the card's 48h window regardless
    // of when the suite happens to run.
    const floor15 = (ms: number) => Math.floor(ms / (15 * 60000)) * (15 * 60000);
    const now = Date.now();
    const s1 = floor15(now + 2 * 3600000);
    const s2 = s1 + 15 * 60000;
    const deadlineIso = new Date(floor15(now + 20 * 3600000)).toISOString();

    await page.route("**/api/car/plan", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          enabled: true,
          car_meter_configured: true,
          soc: {
            soc_pct: 42.3, anchor_pct: 40, anchor_ts: new Date(now - 80 * 3600000).toISOString(),
            added_kwh: 1.2, sessions_since_anchor: 1, age_hours: 80, stale: true,
          },
          plan: {
            soc: 42.3,
            deadlines: [
              { ready_by: deadlineIso, min_pct: 80, required_kwh: 3.33, planned_kwh: 3.33,
                pending_kwh: 0, shortfall_kwh: 0, already_met: false, feasible: true },
            ],
            slots: [
              { start: new Date(s1).toISOString(), kw: 7.4, ac_kwh: 1.85, battery_kwh: 1.67,
                eur_per_kwh_effective: 0.18, est_cost_eur: 0.33, solar_surplus: false,
                for_deadline: deadlineIso },
              { start: new Date(s2).toISOString(), kw: 7.4, ac_kwh: 1.85, battery_kwh: 1.67,
                eur_per_kwh_effective: 0.05, est_cost_eur: 0.09, solar_surplus: true,
                for_deadline: deadlineIso },
            ],
            windows: [
              { start: new Date(s1).toISOString(), end: new Date(s2 + 15 * 60000).toISOString(),
                ac_kwh: 3.7, battery_kwh: 3.33, est_cost_eur: 0.42, solar_share_pct: 50,
                reason: "Cheapest slots to reach 80%." },
            ],
            advice: "Plug in this afternoon to reach 80% by tomorrow.",
            negative_price_hint:
              "Prices go negative Tue 13:00–14:30 — you would be PAID to top up beyond the " +
              "weekly minimum.",
            total_est_cost_eur: 0.42, total_planned_kwh: 3.33,
          },
        }),
      }),
    );
    await page.goto("/");
    const card = page.getByTestId("car-card");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("car-soc-value")).toHaveText("42.3%");
    await expect(page.getByTestId("car-soc-stale")).toBeVisible();
    await expect(page.getByTestId("car-next-deadline")).toContainText("≥80%");
    await expect(page.getByTestId("car-advice")).toContainText("Plug in this afternoon");
    await expect(page.getByTestId("car-negative-price-hint")).toContainText(
      "Prices go negative Tue 13:00–14:30",
    );
    await expect(page.getByTestId("car-window-row").first()).toContainText("3.3 kWh");
    await expect(page.getByTestId("car-window-row").first()).toContainText("50% sun");
    // The 48h strip is always the full 192-cell grid; allocated slots are overlaid on it, a solar
    // slot distinguished from a plain one by class (each also carries a title/tooltip — never
    // color alone).
    await expect(page.getByTestId("car-timeline-cell")).toHaveCount(192);
    await expect(page.locator(".car-cell-solar")).toHaveCount(1);
    await expect(page.locator(".car-cell-fill")).toHaveCount(1);
  });

  test("the demo home shows a persistent nudge into real onboarding that dismisses", async ({
    page,
  }) => {
    // The mock backend runs on simulated data (home_state.simulated = true) → the demo CTA shows.
    await page.goto("/");
    const cta = page.getByTestId("demo-cta");
    await expect(cta).toBeVisible();
    await expect(cta).toContainText("demo home");
    // The link opens Settings (which lands on the Connection section by default).
    await page.getByTestId("demo-cta-link").click();
    await expect(page.getByTestId("nav-settings")).toHaveClass(/nav-active/);
    await expect(page.getByTestId("settings")).toBeVisible();
    // Back to the dashboard: still there (dismiss is per-session, not per-navigation).
    await page.getByTestId("nav-dashboard").click();
    await expect(page.getByTestId("demo-cta")).toBeVisible();
    // Dismiss → gone for the session.
    await page.getByTestId("demo-cta-dismiss").click();
    await expect(page.getByTestId("demo-cta")).toHaveCount(0);
    // Still gone after navigating away and back (sessionStorage holds within the session).
    await page.getByTestId("nav-insights").click();
    await page.getByTestId("nav-dashboard").click();
    await expect(page.getByTestId("demo-cta")).toHaveCount(0);
  });

  test("a barely-started day shows calm dashes, not red zeros (early state)", async ({ page }) => {
    // Production finding: at 00:30 the pills showed red 0s ("Leaning on the grid") — a night
    // reading is not a verdict. partial day + <1 kWh measured → neutral dash state.
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          period: "day", label: "today", partial: true,
          window_start: "2026-07-13T00:00:00+02:00", window_end: "2026-07-14T00:00:00+02:00",
          flows: { has_data: true, home_kwh: 0.2, solar_kwh: 0.0, grid_import_kwh: 0.2,
                   self_sufficiency_pct: 0.0 },
          scores: [
            { key: "self_consumption", label: "Self-consumption", value: 0, raw: null, unit: "%", explanation: "x" },
            { key: "co2", label: "CO2", value: 0, raw: 0.1, unit: "kg", explanation: "x" },
            { key: "best_price", label: "Best price", value: 100, raw: 0.2, unit: "€", explanation: "x" },
          ],
        }),
      }),
    );
    await page.goto("/");
    const pill = page.getByTestId("score-card-self_consumption");
    await expect(pill).toBeVisible();
    await expect(pill).toHaveAttribute("data-state", "early");
    await expect(pill.getByTestId("ring-self_consumption")).toContainText("—");
    await expect(pill).toContainText("The day's just starting");
    await expect(page.getByTestId("home-scores-summary")).toContainText("day's just starting");
    // Early state nulls EVERY pill (a night reading is no verdict for any score).
    const best = page.getByTestId("score-card-best_price");
    await expect(best.getByTestId("ring-best_price")).toContainText("—");
  });

  test("a 3-digit ring value gets the fit class (the '100' clipping fix)", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          period: "day", label: "today", partial: true,
          flows: { has_data: true, home_kwh: 5.0, solar_kwh: 6.0, self_sufficiency_pct: 90 },
          scores: [
            { key: "self_consumption", label: "Self-consumption", value: 100, raw: null, unit: "%", explanation: "x" },
          ],
        }),
      }),
    );
    await page.goto("/");
    const pill = page.getByTestId("score-card-self_consumption");
    await expect(pill).toBeVisible();
    await expect(pill.locator(".ring-value")).toHaveClass(/ring-value-3/);
    await expect(pill.locator(".ring-value")).toContainText("100");
  });

  test("the score pills link through to Insights", async ({ page }) => {
    await page.goto("/");
    const pills = page.getByTestId("home-scores");
    await expect(pills).toBeVisible();
    // Each pill is a button carrying its score value + copy, opening Insights on tap.
    const pill = page.getByTestId("score-card-self_consumption");
    await expect(pill).toBeVisible();
    await pill.click();
    await expect(page.getByTestId("insights")).toBeVisible();
    await expect(page.getByTestId("nav-insights")).toHaveClass(/nav-active/);
  });

  test("an alert with safe + action fields renders structured sub-lines", async ({ page }) => {
    // B-37 contract: alerts may carry optional `safe` (is-my-home-safe) and `action` (what-I-can-do)
    // fields; when present the UI renders them as sub-lines, defensively skipping either if absent.
    await page.route("**/api/alerts", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data_quality: "degraded",
          alerts: [
            {
              key: "solar_stale",
              severity: "warning",
              message: "Solar reading delayed — solar accounting is less precise.",
              safe: "Yes — this only affects solar accounting, not battery safety or control.",
              action: "Nothing needed — EMS keeps controlling the battery normally.",
            },
            {
              // Info-level: stays ONE calm line even when safe/action exist — reassurance
              // sub-lines are reserved for warning/critical (calm states stay calm).
              key: "bare_note",
              severity: "info",
              message: "A plain note with no extra fields.",
              safe: "Should never render for info.",
              action: "Should never render for info.",
            },
          ],
        }),
      }),
    );
    await page.goto("/");
    const alert = page.getByTestId("alert-solar_stale");
    await expect(alert).toContainText("Solar reading delayed");
    await expect(alert.getByTestId("alert-safe")).toContainText(
      "only affects solar accounting",
    );
    await expect(alert.getByTestId("alert-action")).toContainText("Nothing needed");
    // The field-less alert renders its message and NO sub-lines.
    const bare = page.getByTestId("alert-bare_note");
    await expect(bare).toContainText("A plain note with no extra fields.");
    await expect(bare.getByTestId("alert-safe")).toHaveCount(0);
    await expect(bare.getByTestId("alert-action")).toHaveCount(0);
  });

  test("shows the error banner when the status API returns 500", async ({ page }) => {
    await page.route("**/api/status", (route) =>
      route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' }),
    );
    await page.goto("/");
    await expect(page.getByTestId("error")).toBeVisible();
    // The live-status-dependent detail (Advanced + its tiles) stays hidden when status can't load.
    await expect(page.getByTestId("advanced")).toHaveCount(0);
  });

  // B-20: the header bell — an in-app surface for the notification outbox.
  test("the header bell shows an unread dot and opens a dropdown with recent notifications", async ({
    page,
  }) => {
    await page.route(/\/api\/notifications(\?.*)?$/, (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          unread: 1,
          items: [
            {
              id: 2, ts: new Date().toISOString(), key: "backup_failed", title: "Backup failed",
              body: "Today's scheduled backup didn't complete. Your data is safe.",
              confidence: null, read: false, delivered: ["in_app"], dedupe_key: "backup_failed:x",
            },
            {
              id: 1, ts: new Date(Date.now() - 3600e3).toISOString(), key: "backup_failed",
              title: "Backup failed", body: "An earlier failure.", confidence: null, read: true,
              delivered: ["in_app", "ntfy"], dedupe_key: "backup_failed:y",
            },
          ],
        }),
      }),
    );
    await page.goto("/");
    const bell = page.getByTestId("notif-bell");
    await expect(bell).toBeVisible();
    await expect(page.getByTestId("notif-unread-dot")).toBeVisible();
    await expect(page.getByTestId("notif-panel")).toHaveCount(0);

    await bell.click();
    const panel = page.getByTestId("notif-panel");
    await expect(panel).toBeVisible();
    await expect(bell).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByTestId("notif-item-2")).toContainText("Backup failed");
    await expect(page.getByTestId("notif-item-1")).toContainText("An earlier failure.");

    // Esc closes the dropdown.
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(bell).toHaveAttribute("aria-expanded", "false");
  });

  test("marking all notifications read POSTs and clears the unread dot", async ({ page }) => {
    await page.route(/\/api\/notifications(\?.*)?$/, (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          unread: 1,
          items: [{
            id: 1, ts: new Date().toISOString(), key: "backup_failed", title: "Backup failed",
            body: "Today's scheduled backup didn't complete.", confidence: null, read: false,
            delivered: ["in_app"], dedupe_key: "backup_failed:x",
          }],
        }),
      }),
    );
    let readRequestBody: string | null = null;
    await page.route("**/api/notifications/read", async (route) => {
      readRequestBody = route.request().postData();
      await route.fulfill({ status: 200, contentType: "application/json", body: '{"unread":0}' });
    });
    await page.goto("/");
    await page.getByTestId("notif-bell").click();
    await page.getByTestId("notif-mark-all-read").click();
    await expect(page.getByTestId("notif-unread-dot")).toHaveCount(0);
    expect(JSON.parse(readRequestBody ?? "{}")).toEqual({ all: true });
  });

  test("the bell shows no unread dot when there are no notifications", async ({ page }) => {
    await page.route(/\/api\/notifications(\?.*)?$/, (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ unread: 0, items: [] }),
      }),
    );
    await page.goto("/");
    await expect(page.getByTestId("notif-bell")).toBeVisible();
    await expect(page.getByTestId("notif-unread-dot")).toHaveCount(0);
    await page.getByTestId("notif-bell").click();
    await expect(page.getByTestId("notif-empty")).toContainText("No notifications yet.");
  });
});
