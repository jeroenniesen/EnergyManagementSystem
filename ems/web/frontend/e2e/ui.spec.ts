import { expect, test } from "@playwright/test";

test.describe("EMS dashboard", () => {
  test("the whole dashboard explains itself (all panels render together)", async ({ page }) => {
    // A first-time viewer sees status, the strategy, the energy story (timeline), the controller
    // decision, freshness and data-quality on one screen, with no error banner.
    await page.goto("/");
    for (const id of [
      "run-mode-badge",
      "data-quality",
      "home-state",
      "status-grid",
      "strategy-card",
      "energy-story",
      "decision",
      "freshness",
      "alerts",
    ]) {
      await expect(page.getByTestId(id), `panel ${id} should render`).toBeVisible();
    }
    await expect(page.getByTestId("error")).toHaveCount(0);
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

    // The status grid renders, including the reconstructed house-load value (1.00 kW).
    const grid = page.getByTestId("status-grid");
    await expect(grid).toBeVisible();
    await expect(grid).toContainText("House load");
    await expect(grid).toContainText("1.00 kW");
    await expect(grid).toContainText("55 %");
    await expect(grid).toContainText("Battery mode");
    await expect(grid).toContainText("auto");
    await expect(grid).toContainText("Saved today");
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
    await expect(fr).toContainText("Grid meter: up to date");
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
