import { expect, test } from "@playwright/test";

test.describe("EMS API", () => {
  test("favicon is served (no console 404)", async ({ request }) => {
    const r = await request.get("/favicon.svg");
    expect(r.ok()).toBeTruthy();
    expect(r.headers()["content-type"]).toContain("svg");
  });

  test("health endpoints", async ({ request }) => {
    const live = await request.get("/health/live");
    expect(live.ok()).toBeTruthy();
    expect((await live.json()).status).toBe("alive");

    const ready = await request.get("/health/ready");
    expect(ready.ok()).toBeTruthy();
    const rb = await ready.json();
    expect(rb).toMatchObject({ status: "ready", dry_run: true, dev_mode: "mock" });
  });

  test("status reconstructs house load", async ({ request }) => {
    const r = await request.get("/api/status");
    expect(r.ok()).toBeTruthy();
    const b = await r.json();
    // MockSource: grid 200 + solar 0 + battery 800 = 1000 W
    expect(b.house_load_w).toBe(1000);
    expect(b.non_ev_load_w).toBe(1000);
    expect(b.soc_pct).toBe(55);
    expect(b.dry_run).toBe(true);
  });

  test("series returns raw and derived arrays", async ({ request }) => {
    const r = await request.get("/api/series");
    expect(r.ok()).toBeTruthy();
    const b = await r.json();
    expect(Array.isArray(b.raw)).toBeTruthy();
    expect(Array.isArray(b.derived)).toBeTruthy();
  });

  test("series rejects out-of-range limit", async ({ request }) => {
    expect((await request.get("/api/series?limit=0")).status()).toBe(422);
    expect((await request.get("/api/series?limit=999999")).status()).toBe(422);
  });

  test("freshness reports per-signal state after the startup sample", async ({ request }) => {
    const r = await request.get("/api/freshness");
    expect(r.ok()).toBeTruthy();
    const b = await r.json();
    expect(Object.keys(b)).toEqual(
      expect.arrayContaining(["grid", "solar", "ev", "battery", "soc"]),
    );
    expect(b.grid).toBe("fresh"); // recorder took an awaited startup sample
  });

  test("series contains at least the startup sample", async ({ request }) => {
    const b = await (await request.get("/api/series")).json();
    expect(b.raw.length).toBeGreaterThanOrEqual(1);
    expect(b.derived.length).toBeGreaterThanOrEqual(1);
  });

  test("prices returns 15-min slots and a current price", async ({ request }) => {
    const b = await (await request.get("/api/prices")).json();
    expect(b.resolution).toBe("quarter_hourly");
    expect(b.slots.length).toBe(192);
    expect(typeof b.current_eur_per_kwh).toBe("number");
    expect(b.slots[0]).toHaveProperty("eur_per_kwh");
  });

  test("forecast returns P10<=P50<=P90 slots and today kWh", async ({ request }) => {
    const b = await (await request.get("/api/forecast")).json();
    expect(b.slots.length).toBe(192);
    expect(typeof b.today_kwh_p50).toBe("number");
    const s = b.slots[48]; // midday-ish
    expect(s.p10_w).toBeLessThanOrEqual(s.p50_w);
    expect(s.p50_w).toBeLessThanOrEqual(s.p90_w);
  });

  test("plan returns BatteryIntent slots and a current intent", async ({ request }) => {
    const b = await (await request.get("/api/plan")).json();
    expect(b.slots.length).toBeGreaterThan(0);
    expect([
      "allow_self_consumption", "grid_charge_to_target", "hold_reserve", "discharge_for_load",
    ]).toContain(b.current_intent);
    expect(b.slots[0]).toHaveProperty("reason");
  });

  test("battery exposes current mode and probed capabilities", async ({ request }) => {
    const b = await (await request.get("/api/battery")).json();
    expect(["auto", "charge", "discharge", "idle"]).toContain(b.current_mode);
    expect(b.capabilities.services).toContain("charge");
    expect(b.capabilities.p1_paired).toBe(true);
  });

  test("decision is dry-run (no writes) in dev mode", async ({ request }) => {
    const b = await (await request.get("/api/decision")).json();
    expect(b.outcome).toBe("dry_run");
    expect(b.applied).toBe(false);
    expect(b.reason).toContain("dry-run");
  });

  test("alerts reports a data-quality level and the dry-run alert", async ({ request }) => {
    const b = await (await request.get("/api/alerts")).json();
    expect(["complete", "degraded", "price_fallback", "unsafe"]).toContain(b.data_quality);
    expect(b.alerts.some((a: { key: string }) => a.key === "dry_run_active")).toBe(true);
  });

  test("export returns a CSV download with the expected header", async ({ request }) => {
    const r = await request.get("/api/export?kind=raw&format=csv");
    expect(r.ok()).toBeTruthy();
    expect(r.headers()["content-type"]).toContain("text/csv");
    expect(r.headers()["content-disposition"]).toContain("attachment");
    expect((await r.text()).split("\n")[0].trim()).toBe(
      "ts,grid_power_w,solar_power_w,battery_power_w,ev_power_w,soc_pct",
    );
  });

  test("export rejects an invalid kind", async ({ request }) => {
    expect((await request.get("/api/export?kind=bogus")).status()).toBe(422);
  });

  test("savings returns a non-negative estimate", async ({ request }) => {
    const b = await (await request.get("/api/savings")).json();
    expect(typeof b.today_eur).toBe("number");
    expect(b.today_eur).toBeGreaterThanOrEqual(0);
  });
});
