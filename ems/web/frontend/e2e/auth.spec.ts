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
  // The authenticated app shell rendered (not the login screen). Assert the always-visible nav
  // rather than a specific card — the calm-dashboard keeps the plan behind a collapsed disclosure.
  await expect(page.getByTestId("nav-manage")).toBeVisible();

  // Logged in → Manage → Settings: the retired paste-token box is gone, replaced by Logout.
  await page.getByTestId("nav-manage").click();
  await expect(page.getByTestId("access-token")).toHaveCount(0);
  await page.getByTestId("logout").click();
  await expect(page.getByTestId("login")).toBeVisible();
});

// Runs AFTER the test above (same file, same per-file DB, declaration order — see the file-level
// comment): the admin account it created is the one used here to mint the invite.
test("invite-accept creates a new account and logs it in", async ({ page, request }) => {
  // The previous test ended logged out — mint a fresh admin session token via the API directly
  // (no browser needed for this part) rather than re-driving the login form.
  const loginResp = await request.post("/api/auth/login", {
    data: { username: "admin", password: "pw12345678" },
  });
  expect(loginResp.ok()).toBeTruthy();
  const { token: adminToken } = await loginResp.json();

  const inviteResp = await request.post("/api/invites", {
    headers: { Authorization: `Bearer ${adminToken}` },
    data: { role: "user" },
  });
  expect(inviteResp.ok()).toBeTruthy();
  const invite = await inviteResp.json();
  expect(invite.accept_url).toContain("/#/accept-invite?code=");

  // A clean browser for the invitee: no leftover admin token.
  await page.goto("/");
  await page.evaluate(() => localStorage.removeItem("ems.token"));
  await page.goto(invite.accept_url);
  await expect(page.getByTestId("accept-invite")).toBeVisible();
  await page.getByLabel("Username").fill("invited-user");
  await page.getByLabel("Password").fill("invited-pw-12345");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByTestId("accept-invite")).toBeHidden();

  // Same gated-read 200 pattern as the onboarding/login test above: a network-layer proof the
  // bearer token attached and the app is actually rendering the authenticated shell, not a 401'd
  // skeleton that happens to look empty.
  const okResp = await page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/status" && r.status() === 200,
    { timeout: 15000 },
  );
  expect(okResp.ok()).toBe(true);
  await expect(page.getByTestId("nav-manage")).toBeVisible();

  // The invite carried role "user" (not admin) — the admin-only Access & security panel is absent.
  await page.getByTestId("nav-manage").click();
  await expect(page.getByTestId("admin-users")).toHaveCount(0);
});

// Runs AFTER the two tests above (same file, same per-file DB, declaration order — see the
// file-level comment): the "admin" account from the first test still exists, log back in fresh.
test("account tokens: mint shows the raw once, works as a bearer, revoke kills it", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(page.getByTestId("login")).toBeVisible();
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password").fill("pw12345678");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("login")).toBeHidden();
  // Same network-layer proof as the earlier tests: wait for a gated read to actually succeed
  // before touching the authenticated shell.
  await page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/status" && r.status() === 200,
    { timeout: 15000 },
  );

  await page.getByTestId("nav-manage").click();
  const panel = page.getByTestId("account-tokens");
  await expect(panel).toBeVisible();
  // A session-kind principal (interactive login, not a machine token) gets the manage UI, not the
  // sign-in hint that gates a machine/access-token caller (see the "app" project test).
  await expect(page.getByTestId("account-tokens-hint")).toHaveCount(0);

  await page.getByLabel("Name").fill("test-token");
  await page.getByRole("button", { name: "Create" }).click();

  // The raw is shown exactly once, right after minting.
  const minted = page.getByTestId("account-token-minted");
  await expect(minted).toBeVisible();
  const raw = await page.getByLabel("New API token").inputValue();
  expect(raw.length).toBeGreaterThan(20);

  // Copy button works (falls back gracefully if clipboard permissions are unavailable in CI —
  // same convention as admin.spec.ts's invite-link copy test).
  await page.getByRole("button", { name: "Copy" }).click();

  // It shows up in the list.
  const list = page.getByTestId("account-tokens-list");
  await expect(list).toBeVisible();
  await expect(list).toContainText("test-token");

  // The minted raw actually works as a bearer against the real API.
  const okResp = await request.get("/api/status", {
    headers: { Authorization: `Bearer ${raw}` },
  });
  expect(okResp.status()).toBe(200);

  // Revoke it — the row disappears (best-effort refetch after the DELETE) — and the raw stops
  // working immediately.
  const row = list.locator('[data-testid^="account-token-"]', { hasText: "test-token" });
  await row.getByRole("button", { name: /Revoke/ }).click();
  await expect(row).toHaveCount(0);

  const revokedResp = await request.get("/api/status", {
    headers: { Authorization: `Bearer ${raw}` },
  });
  expect(revokedResp.status()).toBe(401);
});

test("account tokens: tier selector defaults to read-only and minted tokens show a tier badge",
  async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("login")).toBeVisible();
    await page.getByLabel("Username").fill("admin");
    await page.getByLabel("Password").fill("pw12345678");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByTestId("login")).toBeHidden();
    await page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/status" && r.status() === 200,
      { timeout: 15000 },
    );

    await page.getByTestId("nav-manage").click();
    await expect(page.getByTestId("account-tokens")).toBeVisible();

    const tier = page.getByTestId("account-token-tier");
    await expect(tier).toBeVisible();
    await expect(tier).toHaveValue("view"); // default read-only

    await page.getByLabel("Name").fill("e2e read-only token");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByTestId("account-token-minted")).toBeVisible();

    // the new row carries a Read-only badge
    const badge = page.getByTestId("account-token-tier-badge").filter({ hasText: "Read-only" });
    await expect(badge.first()).toBeVisible();
  });
