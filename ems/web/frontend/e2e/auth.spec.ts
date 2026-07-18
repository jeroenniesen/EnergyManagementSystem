import { expect, test } from "@playwright/test";

// These two tests share the ONE hermetic webServer/DB for this file (see playwright.config.ts —
// the DB is wiped once per run, not per test) and Playwright runs tests within a file in
// declaration order, in one worker, by default. So the "fresh DB" contract check below MUST stay
// first, before the onboarding flow creates the admin — reordering these breaks that assumption.

test("discovery reports onboarding needed on a fresh database", async ({ request }) => {
  // Task 10 supersedes the old "dev mode has no auth" 2-key legacy shape: `ems.main:app` always
  // wires the identity auth store, so a fresh DB reports the full 5-key discovery payload with
  // onboarding forced (Task 7/9 — GET /api/auth is EXEMPT, so this is reachable logged-out).
  const b = await (await request.get("/api/auth")).json();
  expect(b).toMatchObject({
    required: true,
    authenticated: false,
    onboarding_needed: true,
    user: null,
    shared_token_required: false,
  });
});

test("onboarding then login then logout", async ({ page }) => {
  // fresh DB (see e2e clean-DB harness). First load → onboarding.
  await page.goto("/");
  await expect(page.getByTestId("onboarding")).toBeVisible();
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password").fill("pw12345678");
  await page.getByRole("button", { name: "Create admin" }).click();
  await expect(page.getByTestId("onboarding")).toBeHidden();

  // reload after clearing the token → login screen
  await page.evaluate(() => localStorage.removeItem("ems.token"));
  await page.reload();
  await expect(page.getByTestId("login")).toBeVisible();
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password").fill("pw12345678");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("login")).toBeHidden();

  // Task 11 regression guard, asserted at the NETWORK layer: before the apiFetch retrofit every
  // dashboard card fetched its /api/* data WITHOUT the bearer token, so the identity gate 401'd them
  // all and the dashboard rendered empty. We wait for a GATED dashboard-poll read (`/api/status`) to
  // return **200** — that can only happen once the token is attached, so it can't false-pass. We
  // filter on status()===200 (not just the path) because the pre-login mount tick 401s first, and we
  // assert at the network layer rather than the DOM because a 401'd card degrades silently to its
  // loading skeleton (a DOM check can't tell "loaded" from "401'd").
  const okResp = await page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/status" && r.status() === 200,
    { timeout: 15000 },
  );
  expect(okResp.ok()).toBe(true);
  await expect(page.getByTestId("battery-plan")).toBeVisible();

  // Logged in → Manage → Settings: the retired paste-token box is gone, replaced by Logout.
  await page.getByTestId("nav-manage").click();
  await expect(page.getByTestId("access-token")).toHaveCount(0);
  await page.getByTestId("logout").click();
  await expect(page.getByTestId("login")).toBeVisible();
});
