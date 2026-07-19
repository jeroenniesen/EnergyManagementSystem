import { expect, test } from "@playwright/test";

const REPORT = {
  period: "day",
  window_start: "2026-06-28T00:00:00+00:00",
  window_end: "2026-06-29T00:00:00+00:00",
  label: "2026-06-28",
  partial: false,
  flows: {
    date: "2026-06-28", has_data: true, partial: false,
    solar_to_home: 4, solar_to_car: 1, solar_to_battery: 3, solar_to_grid: 2,
    grid_to_home: 1, grid_to_car: 0.5, grid_to_battery: 0.5,
    battery_to_home: 2.5, battery_to_car: 0, battery_to_grid: 0,
    solar_kwh: 10, grid_import_kwh: 2, grid_export_kwh: 2,
    battery_charge_kwh: 3.5, battery_discharge_kwh: 2.5, home_kwh: 7.5, car_kwh: 1.5,
    self_sufficiency_pct: 80, solar_self_consumption_pct: 80, car_guard_leak_kwh: 0,
  },
  scores: [
    { key: "self_consumption", label: "Self-consumption", value: 80, raw: 80, unit: "%",
      explanation: "Kept 80% of your solar on-site; exported 2.0 kWh you couldn't use or store." },
    { key: "co2", label: "CO₂", value: 60, raw: 1.6, unit: "kg",
      explanation: "Avoided 60% of a no-solar home's CO₂ (2 kg vs 4 kg)." },
    { key: "best_price", label: "Best price", value: 75, raw: 0.13, unit: "€/kWh",
      explanation: "Imported at €0.13/kWh vs the period's €0.08–€0.30 range; ≈ €0.30 saved." },
  ],
};

// B-06 trend chips: the app fetches the SAME period one step back using its own local-calendar
// date math (Insights.tsx's shiftAnchor). Mirror that math here so the mock can tell "today"'s
// request apart from "yesterday"'s regardless of which real day the test happens to run on.
function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}
function todayStr(): string {
  return ymd(new Date());
}
function shiftDay(d: string, delta: number): string {
  const dt = new Date(`${d}T00:00:00`);
  dt.setDate(dt.getDate() + delta);
  return ymd(dt);
}

test.describe("Insights", () => {
  test("shows the three scores and the energy-flow amounts", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("insights")).toBeVisible();
    await expect(page.getByTestId("insights-headline")).toContainText("80% on your own");
    await expect(page.getByTestId("score-grid")).toBeVisible();
    // Three self-explaining 0-100 tiles.
    await expect(page.getByTestId("score-self_consumption-value")).toContainText("80");
    await expect(page.getByTestId("score-co2-value")).toContainText("60");
    await expect(page.getByTestId("score-best_price-value")).toContainText("75");
    await expect(page.getByTestId("score-co2")).toContainText("Avoided 60%");
    // Screen-reader label states the score in words (not just the visual "60/100").
    await expect(page.getByTestId("score-co2")).toHaveAttribute("aria-label", /60 out of 100/);
    // The flow amounts the user asked for (from solar/grid/battery → house/car).
    const flow = page.getByTestId("flow-report");
    await expect(flow).toContainText("Solar");
    await expect(flow).toContainText("Car");
    await expect(flow).toContainText("10.0 kWh"); // solar total
    await expect(page.getByTestId("error")).toHaveCount(0);
  });

  test("the home screen shows today's score cards that open Insights", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.goto("/");
    await page.getByTestId("home-more-toggle").click();
    await expect(page.getByTestId("home-scores")).toBeVisible();
    await expect(page.getByTestId("score-card-self_consumption")).toHaveCount(0);
    const co2Card = page.getByTestId("score-card-co2");
    await expect(page.getByTestId("score-card-co2")).toBeVisible();
    await expect(page.getByTestId("score-card-best_price")).toBeVisible();
    await expect(page.getByTestId("ring-co2")).toContainText("60"); // the score value in the ring
    // The reflective layer: a warm day summary + a band-aware headline + caption on each card.
    const summary = page.getByTestId("home-scores-summary");
    await expect(summary).toHaveAttribute("data-tone", "good"); // 80/60/75 → solid, not brilliant
    await expect(summary).toContainText("solid energy day");
    await expect(co2Card).toContainText("Cleaner than the grid");
    // The whole card is a button whose accessible name carries the score + copy.
    await expect(co2Card).toHaveAttribute(
      "aria-label",
      /60 out of 100.*Cleaner than the grid/,
    );
    // Tapping a card opens the Insights tab.
    await co2Card.click();
    await expect(page.getByTestId("insights")).toBeVisible();
  });

  test("a clean day is celebrated (all scores high → a brilliant-day summary)", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          ...REPORT,
          scores: REPORT.scores.map((s) => ({ ...s, value: 90 })),
        }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("home-more-toggle").click();
    const summary = page.getByTestId("home-scores-summary");
    await expect(summary).toHaveAttribute("data-tone", "great");
    await expect(summary).toContainText("brilliant day");
    // Every card reads as a win.
    await expect(page.getByTestId("score-card-co2")).toContainText("Barely any fossil power");
    await expect(page.getByTestId("score-card-best_price")).toContainText("Bought at the right times");
  });

  // B-06: "you vs last {period}" — a small trend chip per score card, fetched via one extra,
  // best-effort call for the same period one step back.
  test("B-06: shows an up trend chip when this period beats the previous one", async ({ page }) => {
    const today = todayStr();
    const yesterday = shiftDay(today, -1);
    await page.route("**/api/report**", (route) => {
      const date = new URL(route.request().url()).searchParams.get("date");
      const body =
        date === yesterday
          ? { ...REPORT, scores: REPORT.scores.map((s) =>
              s.key === "self_consumption" ? { ...s, value: 74 } : s) }
          : REPORT; // today: self_consumption 80 (REPORT's default)
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const trend = page.getByTestId("score-self_consumption-trend");
    await expect(trend).toBeVisible();
    await expect(trend).toContainText("▲ +6 vs last day");
  });

  test("B-06: shows a muted-amber down trend chip (never a red alarm) when this period is worse", async ({
    page,
  }) => {
    const today = todayStr();
    const yesterday = shiftDay(today, -1);
    await page.route("**/api/report**", (route) => {
      const date = new URL(route.request().url()).searchParams.get("date");
      const body =
        date === yesterday
          ? { ...REPORT, scores: REPORT.scores.map((s) => (s.key === "co2" ? { ...s, value: 64 } : s)) }
          : REPORT; // today: co2 60
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const trend = page.getByTestId("score-co2-trend");
    await expect(trend).toBeVisible();
    await expect(trend).toContainText("▼ −4 vs last day");
    await expect(trend).toHaveClass(/score-trend-down/);
    // Down is muted amber styling, not the app's red/error class.
    await expect(trend).not.toHaveClass(/error|alert|danger/);
  });

  test("B-06: no trend chip when there's no comparable previous period", async ({ page }) => {
    const today = todayStr();
    const yesterday = shiftDay(today, -1);
    await page.route("**/api/report**", (route) => {
      const date = new URL(route.request().url()).searchParams.get("date");
      if (date === yesterday) {
        // No history that far back yet.
        return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) });
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("score-self_consumption")).toBeVisible();
    await expect(page.getByTestId("score-self_consumption-trend")).toHaveCount(0);
    await expect(page.getByTestId("score-co2-trend")).toHaveCount(0);
    await expect(page.getByTestId("score-best_price-trend")).toHaveCount(0);
  });

  test("the period picker switches windows", async ({ page }) => {
    await page.route("**/api/report**", (route) => {
      const period = new URL(route.request().url()).searchParams.get("period") ?? "day";
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, period, label: period === "month" ? "2026-06" : "2026-06-28" }),
      });
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await page.getByTestId("period-month").click();
    await expect(page.getByTestId("period-month")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("insights-label")).toHaveText("2026-06");
  });

  test("Insights is addressable and restorable from the URL hash", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.goto("/#insights");
    await expect(page.getByTestId("insights")).toBeVisible();
    await expect(page.getByTestId("nav-insights")).toHaveAttribute("aria-current", "page");
    await page.getByTestId("nav-dashboard").click();
    await expect(page).toHaveURL(/#dashboard$/);
    await page.getByTestId("nav-insights").click();
    await expect(page).toHaveURL(/#insights$/);
  });

  test("shows an empty state when no energy is recorded", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, flows: { ...REPORT.flows, has_data: false } }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("insights-empty")).toBeVisible();
    await expect(page.getByTestId("score-grid")).toHaveCount(0);
  });

  test("flags a car-guard leak when the battery fed the car", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          ...REPORT,
          flows: { ...REPORT.flows, battery_to_car: 0.4, car_guard_leak_kwh: 0.4 },
        }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("leak-warn")).toContainText("into the car");
  });

  test("hides the gas panel when there's no gas data (report.gas is null)", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, gas: null }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("flow-report")).toBeVisible();
    await expect(page.getByTestId("gas-panel")).toHaveCount(0);
  });

  test("shows the gas panel with m³, kWh-equivalent, € and CO₂ when gas data exists", async ({
    page,
  }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          ...REPORT,
          gas: { m3: 10, kwh_eq: 97.7, eur: 14, co2_kg: 17.8 },
        }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const gas = page.getByTestId("gas-panel");
    await expect(gas).toBeVisible();
    await expect(page.getByTestId("gas-m3")).toContainText("10.0 m³");
    await expect(page.getByTestId("gas-kwh")).toContainText("98 kWh");
    await expect(page.getByTestId("gas-eur")).toContainText("€14.00");
    await expect(page.getByTestId("gas-co2")).toContainText("17.8 kg");
    await expect(gas).toContainText("Heating is typically the biggest energy cost left");
  });
});

test.describe("Insights: heating advice (B-11, advice-only)", () => {
  test("no heating-advice panel when there's no gas meter (never nags)", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, gas: null }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("gas-panel")).toHaveCount(0);
    await expect(page.getByTestId("heating-advice")).toHaveCount(0);
  });

  test("shows the three advice cards, the safety line, and an annualised estimate", async ({
    page,
  }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          ...REPORT,
          gas: { m3: 8, kwh_eq: 78.2, eur: 12, co2_kg: 14.2 },
        }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const advice = page.getByTestId("heating-advice");
    await expect(advice).toBeVisible();
    await expect(advice).toContainText("Heating — the biggest lever left");
    const balancing = page.getByTestId("advice-balancing");
    const flowTemp = page.getByTestId("advice-flow-temp");
    const dhw = page.getByTestId("advice-dhw-eco");
    await expect(balancing).toBeVisible();
    await expect(flowTemp).toBeVisible();
    await expect(dhw).toBeVisible();
    // Each card is framed by the window's real gas evidence and carries the disclaimer.
    await expect(balancing).toContainText("8.0 m³ ≈ €12.00");
    await expect(balancing).toContainText("Advice only — nothing changes automatically.");
    await expect(flowTemp).toContainText("Advice only — nothing changes automatically.");
    await expect(dhw).toContainText("Advice only — nothing changes automatically.");
    // Balancing annualises the window's € honestly: €12 * 365/1 * 0.125, rounded to €10 = €550/yr.
    await expect(balancing).toContainText("€550/yr");
    await expect(balancing).toContainText("rough estimate from your meter");
    // The flow-temp card names the Dutch shorthand and gives link-free instructions.
    await expect(flowTemp).toContainText("zet 'm op 60");
    await expect(flowTemp).toContainText("60°C");
    // The DHW card's safety line is explicit: never dips below 60°C, and says why.
    const safety = page.getByTestId("advice-dhw-eco-safety");
    await expect(safety).toContainText("Legionella");
    await expect(safety).toContainText("60°C or higher");
    await expect(safety).toContainText("never set it below 60°C");
    // 8 m³ in a single day is real heating, not summer DHW-only use — no seasonal note.
    await expect(page.getByTestId("heating-advice-seasonal")).toHaveCount(0);
  });

  test("each card's instructions are collapsed by default and open on demand", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          ...REPORT,
          gas: { m3: 8, kwh_eq: 78.2, eur: 12, co2_kg: 14.2 },
        }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const details = page.getByTestId("advice-flow-temp").locator("details.advice-details");
    const instructions = details.locator("p");
    await expect(instructions).toBeHidden();
    await details.locator("summary").click();
    await expect(instructions).toContainText("CV/flow temperature");
  });

  test("shows a muted seasonal note when the window's gas use is low (summer)", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          ...REPORT,
          gas: { m3: 0.4, kwh_eq: 3.9, eur: 0.6, co2_kg: 0.7 },
        }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("heating-advice")).toBeVisible();
    const note = page.getByTestId("heating-advice-seasonal");
    await expect(note).toContainText("barely heating");
    await expect(note).toContainText("pay off from autumn");
  });
});

// Production feedback: "Can't check these items as done." Each advice card can be marked done
// (one-off jobs, not a recurring habit) — state lives in ONE settings field, `heating.done`, saved
// immediately on click (never via the Settings dirty-bar). /api/settings is mocked here (both GET
// and POST) so these tests never touch the shared e2e DB — see e2e-needs-clean-db in project memory.
test.describe("Insights: heating advice — mark as done", () => {
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function doneLabel(iso: string): string {
    const d = new Date(`${iso}T00:00:00`);
    return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  }

  function mockSettings(
    page: import("@playwright/test").Page,
    initialDone: Record<string, string>,
    onPost?: (body: Record<string, unknown>) => void,
  ) {
    return page.route("**/api/settings", async (route) => {
      if (route.request().method() === "POST") {
        onPost?.(JSON.parse(route.request().postData() || "{}"));
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ values: {}, restart_required: false }),
        });
        return;
      }
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ schema: [], values: { "heating.done": JSON.stringify(initialDone) } }),
      });
    });
  }

  test.beforeEach(async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, gas: { m3: 8, kwh_eq: 78.2, eur: 12, co2_kg: 14.2 } }),
      }),
    );
  });

  test("marking a card done collapses it to a done line and POSTs only heating.done", async ({
    page,
  }) => {
    let posted: Record<string, unknown> | undefined;
    await mockSettings(page, {}, (body) => {
      posted = body;
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const card = page.getByTestId("advice-balancing");
    await expect(card).toBeVisible();
    await page.getByTestId("advice-balancing-mark-done").click();

    const today = todayStr();
    await expect(card).toHaveAttribute("data-done", "true");
    await expect(card).toContainText(`✓ Balanced radiators — done ${doneLabel(today)}`);
    await expect(card.getByTestId("advice-balancing-undo")).toBeVisible();
    // The full advice text is gone, not just hidden.
    await expect(card).not.toContainText("Advice only");

    await expect.poll(() => posted).toBeTruthy();
    expect(posted).toEqual({ "heating.done": JSON.stringify({ balancing: today }) });
  });

  test("undo restores the full card and POSTs the item removed from heating.done", async ({
    page,
  }) => {
    let posted: Record<string, unknown> | undefined;
    await mockSettings(page, { balancing: "2026-06-01" }, (body) => {
      posted = body;
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const card = page.getByTestId("advice-balancing");
    await expect(card).toHaveAttribute("data-done", "true");
    await expect(card).toContainText("done 1 Jun");

    await page.getByTestId("advice-balancing-undo").click();
    await expect(card).not.toHaveAttribute("data-done", "true");
    await expect(card).toContainText("Balance your radiators");
    await expect(card).toContainText("Advice only — nothing changes automatically.");
    await expect(page.getByTestId("advice-balancing-mark-done")).toBeVisible();

    await expect.poll(() => posted).toBeTruthy();
    expect(posted).toEqual({ "heating.done": JSON.stringify({}) });
  });

  test("a done card sorts below the not-done cards", async ({ page }) => {
    await mockSettings(page, { flow_temp: "2026-06-01" });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("advice-flow-temp")).toHaveAttribute("data-done", "true");

    const cards = page.locator(".advice-cards > div");
    await expect(cards).toHaveCount(3);
    const order = await cards.evaluateAll((els) => els.map((el) => el.getAttribute("data-testid")));
    expect(order).toEqual(["advice-balancing", "advice-dhw-eco", "advice-flow-temp"]);
  });

  test("the header swaps to the all-done state once every card is marked done", async ({ page }) => {
    await mockSettings(page, {
      balancing: "2026-06-01", flow_temp: "2026-06-01", dhw_eco: "2026-06-01",
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const advice = page.getByTestId("heating-advice");
    await expect(advice).toContainText("Heating — all three done ✓");
    await expect(advice).not.toContainText("Heating — the biggest lever left");
    const alldone = page.getByTestId("heating-advice-alldone");
    await expect(alldone).toContainText("one-offs");
    await expect(alldone).toContainText("revisit if your setup changes");
  });
});

const SERIES = Array.from({ length: 96 }, (_, i) => {
  const h = String(Math.floor(i / 4)).padStart(2, "0");
  const m = String((i % 4) * 15).padStart(2, "0");
  const sampled = i >= 20 && i < 80;
  return {
    start: `2026-06-28T${h}:${m}:00+00:00`,
    grid_import_kwh: sampled ? 0.2 : 0, grid_export_kwh: sampled && i > 40 ? 0.3 : 0,
    house_kwh: sampled ? 0.35 : 0, car_kwh: sampled && i > 60 ? 0.9 : 0,
    solar_kwh: sampled ? 0.4 : 0, samples: sampled ? 3 : 0,
  };
});

const FINANCE = {
  period: "day", label: "2026-06-28", partial: false,
  days: [{
    day: "2026-06-28", has_data: true, price_coverage: 1.0,
    grid_cost_eur: 1.42, battery_cost_eur: 0.11, baseline_cost_eur: 2.31, saved_eur: 0.78,
    grid_import_kwh: 7.4, grid_export_kwh: 2.0,
  }],
  totals: {
    grid_cost_eur: 1.42, battery_cost_eur: 0.11, saved_eur: 0.78,
    grid_import_kwh: 7.4, grid_export_kwh: 2.0, days_with_prices: 1, days_with_data: 1,
  },
};

test.describe("Insights: behavior chart + money", () => {
  test("renders the energy-behavior chart with legend and figures table", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, series: SERIES }) }),
    );
    await page.route("**/api/finance**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify(FINANCE) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const chart = page.getByTestId("energy-behavior");
    await expect(chart).toBeVisible();
    await expect(chart).toContainText("How your energy behaved");
    // Ingress and egress live in SEPARATE panels: in one mixed graph the grid trace is the sum
    // of the consumers, so a charging car and the grid drew exactly on top of each other.
    const consumption = page.getByTestId("behavior-consumption");
    const grid = page.getByTestId("behavior-grid");
    await expect(consumption).toBeVisible();
    await expect(grid).toBeVisible();
    await expect(chart).toContainText("Used by the home");
    await expect(chart).toContainText("Solar & grid");
    // Identity is never color-alone: each panel's legend names its own series.
    for (const name of ["House", "Car"]) {
      await expect(consumption.locator(".chart-legend")).toContainText(name);
    }
    for (const name of ["Solar", "Grid in", "Grid out"]) {
      await expect(grid.locator(".chart-legend")).toContainText(name);
    }
    // A table view of the figures exists (accessibility relief for the chart).
    await chart.locator(".chart-table summary").click();
    await expect(chart.locator(".chart-table table")).toBeVisible();
    await expect(chart.locator(".chart-table tbody tr").first()).toContainText("0.35");
  });

  test("shows measured money totals and per-day context", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, series: SERIES }) }),
    );
    await page.route("**/api/finance**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify(FINANCE) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const fin = page.getByTestId("finance-section");
    await expect(fin).toBeVisible();
    await expect(page.getByTestId("fin-saved")).toContainText("€0.78");
    await expect(page.getByTestId("fin-grid")).toContainText("€1.42");
    await expect(page.getByTestId("fin-wear")).toContainText("€0.11");
    await expect(fin).toContainText("measured, after wear");
  });

  test("is honest when no price history exists yet", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, series: SERIES }) }),
    );
    await page.route("**/api/finance**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({
          ...FINANCE,
          days: [{ ...FINANCE.days[0], price_coverage: 0, grid_cost_eur: null,
                   battery_cost_eur: null, baseline_cost_eur: null, saved_eur: null }],
          totals: { ...FINANCE.totals, grid_cost_eur: null, battery_cost_eur: null,
                    saved_eur: null, days_with_prices: 0 },
        }) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("fin-caveat")).toContainText("No price history recorded yet");
    await expect(page.getByTestId("fin-saved")).toHaveCount(0); // never invent a € figure
  });

  test("keeps Insights useful when money history cannot load", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, series: SERIES }) }),
    );
    await page.route("**/api/finance**", (route) =>
      route.fulfill({ status: 503, contentType: "application/json", body: "{\"detail\":\"down\"}" }),
    );
    await page.goto("/#insights");
    await expect(page.getByTestId("score-grid")).toBeVisible();
    await expect(page.getByTestId("energy-behavior")).toBeVisible();
    await expect(page.getByTestId("fin-error")).toContainText("Money history could not be loaded");
  });
});

const DIGEST = {
  week_label: "Week of 2026-06-22",
  saved_eur: 12.34,
  best_day: { date: "2026-06-25", saved_eur: 3.2 },
  self_sufficiency_pct: 78.4,
  solar_kwh: 24.5,
  co2_avoided_note: "Avoided 62% of a no-solar home's CO₂ (12 kg vs 32 kg).",
  actions: { mode_switches: 3, negative_soaks: 1, overrides: 1 },
  tweak: null, // null case: the callout must be ABSENT (headline tail already says it)
  headline:
    "You saved €12.34 this week, ran 78% self-sufficient and the panels made 24.5 kWh. " +
    "Steady week — settings look right.",
  days_measured: 7,
  days_total: 7,
};

test.describe("Insights: your week digest (B-58)", () => {
  test("shows the headline, saved €, facts and tweak at the top of Insights", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.route("**/api/digest**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(DIGEST) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const panel = page.getByTestId("week-digest");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("week-digest-headline")).toContainText("You saved €12.34");
    await expect(page.getByTestId("week-digest-saved")).toContainText("€12.34");
    await expect(page.getByTestId("week-digest-fact-self-sufficiency")).toContainText("78%");
    await expect(page.getByTestId("week-digest-fact-solar")).toContainText("24.5 kWh");
    await expect(page.getByTestId("week-digest-fact-actions")).toContainText("4"); // 3 switches + 1 override
    await expect(page.getByTestId("week-digest-tweak")).toHaveCount(0); // calm = absence
    await expect(page.getByTestId("week-digest-best-day")).toContainText("2026-06-25");
    await expect(page.getByTestId("week-digest-coverage")).toHaveCount(0); // full week, no caveat
    await expect(page.getByTestId("week-digest-label")).toHaveText("Week of 2026-06-22");
  });

  test("shows the partial-week caveat when fewer than 7 days were measured", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.route("**/api/digest**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...DIGEST, days_measured: 5, days_total: 7 }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("week-digest-coverage")).toContainText("5 of 7 days measured");
  });

  test("collapses to one line (the headline) and expands again", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.route("**/api/digest**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(DIGEST) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const toggle = page.getByTestId("week-digest-toggle");
    await expect(page.getByTestId("week-digest-body")).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await toggle.click();
    await expect(page.getByTestId("week-digest-body")).toHaveCount(0);
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    // The headline (the "one line") stays visible while collapsed.
    await expect(page.getByTestId("week-digest-headline")).toBeVisible();
    await toggle.click();
    await expect(page.getByTestId("week-digest-body")).toBeVisible();
  });

  test("the ‹ › week stepper navigates to the previous and next week", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.route("**/api/digest**", (route) => {
      const week = new URL(route.request().url()).searchParams.get("week");
      const label = week === "2026-06-15" ? "Week of 2026-06-15" : "Week of 2026-06-22";
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...DIGEST, week_label: label }),
      });
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("week-digest-label")).toHaveText("Week of 2026-06-22");
    await page.getByTestId("week-digest-prev").click();
    await expect(page.getByTestId("week-digest-label")).toHaveText("Week of 2026-06-15");
    await page.getByTestId("week-digest-next").click();
    await expect(page.getByTestId("week-digest-label")).toHaveText("Week of 2026-06-22");
  });

  test("stays hidden without blocking the rest of Insights when /api/digest fails", async ({
    page,
  }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.route("**/api/digest**", (route) =>
      route.fulfill({ status: 503, contentType: "application/json", body: "{\"detail\":\"down\"}" }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("week-digest")).toHaveCount(0);
    await expect(page.getByTestId("score-grid")).toBeVisible();
  });
});


test.describe("Insights: day-just-starting honesty", () => {
  const early = {
    period: "day", label: "today", partial: true,
    window_start: "2026-07-13T00:00:00+02:00", window_end: "2026-07-14T00:00:00+02:00",
    flows: { has_data: true, home_kwh: 0.3, solar_kwh: 0, grid_import_kwh: 0.3, grid_export_kwh: 0,
             battery_charge_kwh: 0, battery_discharge_kwh: 0, car_kwh: 0, self_sufficiency_pct: 0,
             solar_self_consumption_pct: 0, car_guard_leak_kwh: 0 },
    scores: [
      { key: "self_consumption", label: "Self-consumption", value: 0, raw: null, unit: "%", explanation: "real explanation" },
      { key: "co2", label: "CO2", value: 0, raw: 0.2, unit: "kg", explanation: "real explanation" },
    ],
  };

  test("a partial day with <1 kWh shows the calm headline and dashes", async ({ page }) => {
    await page.route("**/api/report**", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(early) }));
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("insights-headline")).toContainText("day's just starting");
    const card = page.getByTestId("score-self_consumption");
    await expect(card).toHaveAttribute("data-state", "early");
    await expect(card).toContainText("Waiting for the sun");
  });

  test("a COMPLETED zero day still shows its real zeros (honesty is day-scoped)", async ({ page }) => {
    const done = { ...early, partial: false, label: "2026-07-10" };
    await page.route("**/api/report**", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(done) }));
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("insights-headline")).toContainText("You ran 0%");
    const card = page.getByTestId("score-self_consumption");
    await expect(card).not.toHaveAttribute("data-state", "early");
    await expect(card).toContainText("real explanation");
  });
});

const COUNTERFACTUAL = {
  window: { start: "2026-06-30", end: "2026-07-13", days_requested: 14 },
  days_used: 14,
  days_skipped: 0,
  scenarios: {
    no_battery: { cost_eur: 42.1, import_kwh: 210, export_kwh: 30 },
    auto_selfuse: { cost_eur: 31.5, import_kwh: 150, export_kwh: 10 },
    planner: { cost_eur: 24.2, import_kwh: 120, export_kwh: 8 },
  },
  deltas: { planner_vs_no_battery: 17.9, planner_vs_auto: 7.3 },
  note: "Your setup beat doing nothing by €17.90 over 14 measured days.",
};

const WHATIF_RESULT = {
  simulation: true,
  days: 14,
  days_used: 14,
  days_skipped: 0,
  overrides: { "planner.negative_price_soak": true },
  baseline: { cost_eur: 24.2 },
  variant: { cost_eur: 23.36 },
  delta_eur: 0.84,
  per_day: [
    { date: "2026-07-01", baseline_eur: 1.8, variant_eur: 1.74, delta_eur: 0.06 },
    { date: "2026-07-02", baseline_eur: 1.7, variant_eur: 1.6, delta_eur: 0.1 },
  ],
  note: "This would have saved ≈ €0.84 over the last 14 measured days.",
};

test.describe("Insights: what-if scenario simulator (B-73) + counterfactual (B-69)", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
  });

  test("shows the counterfactual header line and the always-on simulation badge", async ({ page }) => {
    await page.route("**/api/counterfactual**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(COUNTERFACTUAL) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const panel = page.getByTestId("whatif-panel");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("whatif-badge")).toContainText("simulation — nothing is changed");
    await expect(page.getByTestId("whatif-counterfactual")).toContainText("beat no-battery by €17.90");
    await expect(page.getByTestId("whatif-counterfactual")).toContainText("vendor-auto by €7.30");
  });

  test("stays useful when the counterfactual header can't load (best-effort)", async ({ page }) => {
    await page.route("**/api/counterfactual**", (route) =>
      route.fulfill({ status: 503, contentType: "application/json", body: "{\"detail\":\"down\"}" }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("whatif-panel")).toBeVisible();
    await expect(page.getByTestId("whatif-counterfactual")).toHaveCount(0);
    await expect(page.getByTestId("whatif-badge")).toBeVisible();
  });

  test("clicking a preset chip runs the simulation and shows a plain-language verdict", async ({ page }) => {
    await page.route("**/api/counterfactual**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(COUNTERFACTUAL) }),
    );
    let requestBody: unknown;
    await page.route("**/api/whatif", (route) => {
      requestBody = route.request().postDataJSON();
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(WHATIF_RESULT) });
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    const chip = page.getByTestId("whatif-preset-negative-prices");
    await chip.click();
    await expect(chip).toHaveClass(/whatif-chip-active/);
    const verdict = page.getByTestId("whatif-verdict");
    await expect(verdict).toContainText("Charge on negative prices");
    await expect(verdict).toContainText("saved ≈ €0.84");
    await expect(verdict).toContainText("14 measured days");
    await expect(page.getByTestId("whatif-baseline")).toContainText("€24.20");
    await expect(page.getByTestId("whatif-variant")).toContainText("€23.36");
    expect(requestBody).toEqual({ overrides: { "planner.negative_price_soak": true }, days: 14 });
  });

  test("the per-day breakdown is collapsed behind a disclosure by default", async ({ page }) => {
    await page.route("**/api/counterfactual**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(COUNTERFACTUAL) }),
    );
    await page.route("**/api/whatif", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(WHATIF_RESULT) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await page.getByTestId("whatif-preset-bigger-reserve").click();
    const table = page.getByTestId("whatif-per-day");
    await expect(table).toBeVisible();
    await expect(table.locator("tbody tr").first()).toBeHidden(); // collapsed <details>
    await table.locator("summary").click();
    await expect(table.locator("tbody tr")).toHaveCount(2);
    await expect(table.locator("tbody tr").first()).toContainText("2026-07-01");
  });

  test("shows a loading state while the replay runs, then the result", async ({ page }) => {
    await page.route("**/api/counterfactual**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(COUNTERFACTUAL) }),
    );
    await page.route("**/api/whatif", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 300));
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(WHATIF_RESULT) });
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await page.getByTestId("whatif-preset-cautious-forecast").click();
    await expect(page.getByTestId("whatif-loading")).toBeVisible();
    await expect(page.getByTestId("whatif-verdict")).toBeVisible();
    await expect(page.getByTestId("whatif-loading")).toHaveCount(0);
  });

  test("a calm failure message when the simulation errors, without breaking the rest of Insights", async ({
    page,
  }) => {
    await page.route("**/api/counterfactual**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(COUNTERFACTUAL) }),
    );
    await page.route("**/api/whatif", (route) =>
      route.fulfill({ status: 500, contentType: "application/json", body: "{\"detail\":\"boom\"}" }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await page.getByTestId("whatif-preset-post-2027-export").click();
    await expect(page.getByTestId("whatif-error")).toContainText("try again in a moment");
    await expect(page.getByTestId("score-grid")).toBeVisible();
  });

  test("switching the day window re-runs the active preset with the new window", async ({ page }) => {
    await page.route("**/api/counterfactual**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(COUNTERFACTUAL) }),
    );
    const seenDays: number[] = [];
    await page.route("**/api/whatif", (route) => {
      const body = route.request().postDataJSON() as { days: number };
      seenDays.push(body.days);
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...WHATIF_RESULT, days: body.days, days_used: body.days }),
      });
    });
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await page.getByTestId("whatif-preset-negative-prices").click();
    await expect(page.getByTestId("whatif-verdict")).toBeVisible();
    await page.getByTestId("whatif-days-30").click();
    await expect.poll(() => seenDays).toEqual([14, 30]);
    await expect(page.getByTestId("whatif-verdict")).toContainText("30 measured days");
  });
});

test.describe("Insights: score card anatomy (ring | headline | trend | one detail line)", () => {
  // A co2 explanation with a second sentence (the gas footnote) — the "wall of text" side of the
  // production imbalance. self_consumption's explanation is always one sentence — the "almost
  // empty card" side. Both now get the SAME structure: one detail line, extra behind "More".
  const TWO_SENTENCE_CO2 = {
    ...REPORT,
    scores: REPORT.scores.map((s) =>
      s.key === "co2"
        ? {
            ...s,
            explanation: "Avoided 60% of a no-solar home's CO₂ (2 kg vs 4 kg). Gas heating is "
              + "35% of your footprint — the biggest cut left.",
          }
        : s,
    ),
  };

  test("every card shows ring, headline word, trend chip and exactly one detail line", async ({
    page,
  }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();

    const selfCard = page.getByTestId("score-self_consumption");
    await expect(selfCard.getByTestId("score-self_consumption-value")).toBeVisible(); // ring
    await expect(page.getByTestId("score-self_consumption-headline")).toContainText(
      "Mostly your own sun",
    ); // headline word
    await expect(page.getByTestId("score-self_consumption-line")).toContainText(
      "Kept 80% of your solar on-site",
    ); // the one detail line
    // Single-sentence explanation -> nothing left to disclose, no "More" button.
    await expect(selfCard.locator(".help-more")).toHaveCount(0);
  });

  test("a two-sentence explanation (CO₂ + gas) shows only the first sentence, with a More disclosure", async ({
    page,
  }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json", body: JSON.stringify(TWO_SENTENCE_CO2),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();

    const line = page.getByTestId("score-co2-line");
    await expect(line).toContainText("Avoided 60% of a no-solar home's CO₂ (2 kg vs 4 kg).");
    await expect(line).not.toContainText("Gas heating");
    const more = page.getByTestId("score-co2").locator(".help-more");
    await expect(more).toHaveText("More");
    await more.click();
    await expect(line).toContainText("Gas heating is 35% of your footprint");
    await expect(more).toHaveText("Less");
    // The full explanation is ALSO available without tapping, via the ring's own hover tooltip.
    await expect(page.getByTestId("score-co2-value")).toHaveAttribute(
      "title", /Gas heating is 35% of your footprint/,
    );
  });

  test("the ring's inner label is shortened to avoid hyphenation, but the full label reads elsewhere", async ({
    page,
  }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();

    // The ring's own name badge uses the short alias ("Self-consump-tion" hyphenation bug, fixed
    // by shortening rather than fighting the CSS).
    const ring = page.getByTestId("score-self_consumption-value");
    await expect(ring).toContainText("Self-use");
    await expect(ring).not.toContainText("Self-consumption");
    // Every other surface on the SAME card still uses the full label.
    const card = page.getByTestId("score-self_consumption");
    await expect(card).toHaveAttribute("aria-label", /Self-consumption score/);
  });
});

test.describe("Insights: sticky in-page section nav", () => {
  test("stays hidden until scrolled, then shows section links with the active one marked", async ({
    page,
  }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await expect(page.getByTestId("score-grid")).toBeVisible();

    const nav = page.getByTestId("insights-section-nav");
    await expect(nav).toHaveCount(0); // hidden at the top of the page

    await page.mouse.wheel(0, 900);
    await expect(nav).toBeVisible();
    for (const id of ["week", "scores", "energy", "money", "whatif"]) {
      await expect(page.getByTestId(`insights-nav-${id}`)).toBeVisible();
    }
    // REPORT (the base fixture) has no gas data -> no Gas link even though the nav is showing.
    await expect(page.getByTestId("insights-nav-gas")).toHaveCount(0);

    // Clicking a link scrolls to its section without touching the app's own hash-based router
    // (a real `#id` anchor would kick the whole app back to the dashboard — see viewFromHash).
    await page.getByTestId("insights-nav-whatif").click();
    await expect(page).toHaveURL(/#insights$/);
    await expect(page.getByTestId("nav-insights")).toHaveAttribute("aria-current", "page");
    await expect
      .poll(async () => page.getByTestId("insights-nav-whatif").getAttribute("aria-current"))
      .toBe("true");
  });

  test("omits the Gas link when the report has no gas data", async ({ page }) => {
    await page.route("**/api/report**", (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REPORT, gas: null }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("nav-insights").click();
    await page.mouse.wheel(0, 900);
    await expect(page.getByTestId("insights-section-nav")).toBeVisible();
    await expect(page.getByTestId("insights-nav-gas")).toHaveCount(0);
  });
});
