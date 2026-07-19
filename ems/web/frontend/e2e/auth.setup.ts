import { expect, test as setup } from "@playwright/test";

import { E2E_ACCESS_TOKEN } from "./e2e-auth";

// Playwright setup project (a dependency of the "app" project): onboards the first admin on the app
// webServer so every other dashboard/API spec runs authenticated. The app server is started with
// EMS_WEB_TOKEN=E2E_ACCESS_TOKEN, so passing it as `shared_token` here migrates it into an admin
// ACCESS token (design §8). The app project then sends `Authorization: Bearer E2E_ACCESS_TOKEN` on
// every request, which resolves to that admin. auth.spec.ts runs on a SEPARATE tokenless server and
// is excluded from this flow, so its fresh-DB onboarding assertions still hold.
setup("onboard the e2e admin on the app server", async ({ request }) => {
  const r = await request.post("/api/auth/onboard", {
    data: {
      username: "e2e-admin",
      password: "e2e-password-1234",
      shared_token: E2E_ACCESS_TOKEN,
    },
  });
  // 200 on a fresh DB (the app server rm's its DB on start); 409 only if a reused DB was already
  // onboarded — harmless, the migrated token from that run is the same fixed value.
  expect([200, 409]).toContain(r.status());
});
