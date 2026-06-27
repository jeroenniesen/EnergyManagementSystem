import { expect, test } from "@playwright/test";

test.describe("EMS API", () => {
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
});
