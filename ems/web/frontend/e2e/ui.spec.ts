import { expect, type Page, test } from "@playwright/test";

// The detailed panels (power tiles, Sankey, charge target, controller decision, AI note, data
// status) now live in a collapsed "Advanced" section — open it before asserting on them.
async function openAdvanced(page: Page) {
  await page.getByTestId("advanced-toggle").click();
  await expect(page.getByTestId("advanced-body")).toBeVisible();
}

test.describe("EMS dashboard", () => {
  test("the calm home surfaces the essentials, with detail behind Advanced", async ({ page }) => {
    // A first-time viewer sees the state banner, the strategy, the energy story (the plan) and a
    // trimmed status grid up front — the technical detail (decision, freshness, Sankey) is tucked
    // behind the Advanced toggle so the home stays calm. No error banner.
    await page.goto("/");
    for (const id of [
      "run-mode-badge",
      "data-quality",
      "home-state",
      "status-grid",
      "strategy-card",
      "energy-story",
      "advanced",
      "alerts",
    ]) {
      await expect(page.getByTestId(id), `panel ${id} should render`).toBeVisible();
    }
    // The detail is present but not shouted — it appears only once Advanced is opened.
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

  test("the home-state banner leads with a calm headline + confidence", async ({ page }) => {
    await page.goto("/");
    const banner = page.getByTestId("home-state");
    await expect(banner).toBeVisible();
    await expect(banner).toHaveAttribute("data-tone", /good|watching|controlling|attention/);
    await expect(page.getByTestId("home-confidence")).toBeVisible();
  });

  test("renders the status dashboard with reconstructed load", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Smart Energy Manager" })).toBeVisible();

    // Run-mode badge in plain language (dry-run => "Watching only"; M0a is read-only).
    await expect(page.getByTestId("run-mode-badge")).toHaveText("Watching only");

    // The trimmed status grid leads with the essentials: savings, battery level and mode.
    const grid = page.getByTestId("status-grid");
    await expect(grid).toBeVisible();
    await expect(grid).toContainText("55 %");
    await expect(grid).toContainText("Battery mode");
    await expect(grid).toContainText("auto");
    await expect(grid).toContainText("Saved today");
    // The reconstructed house-load value (1.00 kW) lives with the detail metrics behind Advanced.
    await openAdvanced(page);
    const detail = page.getByTestId("detail-grid");
    await expect(detail).toContainText("House load");
    await expect(detail).toContainText("1.00 kW");
  });

  test("no API error banner when backend is up", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("status-grid")).toBeVisible();
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

  test("energy story tells the next-24h plan (headline, SoC, tracks, stats)", async ({ page }) => {
    await page.goto("/");
    const story = page.getByTestId("energy-story");
    await expect(story).toBeVisible();
    await expect(page.getByTestId("story-tag")).toContainText("the plan"); // Next is the default
    await expect(page.getByTestId("story-headline")).toContainText("Next 24h");
    await expect(page.getByTestId("story-soc-line")).toBeAttached();
    await expect(page.getByTestId("story-target")).toBeAttached();
    await expect(page.getByTestId("story-reserve")).toBeAttached();
    await expect(page.getByTestId("story-stats")).toBeVisible();
    await expect(page.getByTestId("story-legend")).toBeVisible();
  });

  test("toggling to Last 24h switches the story", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("story-past").click();
    await expect(page.getByTestId("story-tag")).toContainText("what happened");
    await expect(page.getByTestId("story-headline")).not.toHaveText("");
    // Back to Next.
    await page.getByTestId("story-next").click();
    await expect(page.getByTestId("story-tag")).toContainText("the plan");
  });

  test("the next story shows an on-track verdict", async ({ page }) => {
    // The real (mock) backend always returns an on_track verdict for the next window.
    await page.goto("/");
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
    const verdict = page.getByTestId("on-track");
    await expect(verdict).toHaveAttribute("data-status", "behind");
    await expect(verdict).toContainText("Behind");
    // Both the measured (solid) and forecast (dashed) SoC lines render on the same chart.
    await expect(page.getByTestId("story-soc-actual")).toBeAttached();
    await expect(page.getByTestId("story-soc-line")).toBeAttached();
    // The "did we do right" review (solar vs forecast) is shown.
    await expect(page.getByTestId("recent-review")).toContainText("of the 4.0 kWh forecast");
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
    await expect(page.getByTestId("status-grid")).toHaveCount(0);
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
    await expect(page.getByTestId("status-grid")).toBeVisible();
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

  test("shows the error banner when the status API returns 500", async ({ page }) => {
    await page.route("**/api/status", (route) =>
      route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' }),
    );
    await page.goto("/");
    await expect(page.getByTestId("error")).toBeVisible();
    await expect(page.getByTestId("status-grid")).toHaveCount(0);
  });
});
