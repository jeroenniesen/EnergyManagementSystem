import { fileURLToPath } from "node:url";
import path from "node:path";

import { defineConfig } from "@playwright/test";

import { E2E_ACCESS_TOKEN } from "./e2e/e2e-auth";

// ems/web/frontend -> repo root (portable; no hardcoded absolute path).
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

// Ports are env-overridable so concurrent git worktrees don't collide on a hardcoded port. CI uses
// the defaults (a fresh runner); a second local worktree can set EMS_E2E_APP_PORT / EMS_E2E_AUTH_PORT.
const APP_PORT = Number(process.env.EMS_E2E_APP_PORT ?? 8099);
const AUTH_PORT = Number(process.env.EMS_E2E_AUTH_PORT ?? 8100);
const APP_URL = `http://127.0.0.1:${APP_PORT}`;
const AUTH_URL = `http://127.0.0.1:${AUTH_PORT}`;

// HERMETIC webServer env: fresh throwaway DB (rm'd first) + forced mock sources/prices, so a run
// never reads the operator's persisted settings, never touches live LAN devices, and AI stays off.
const baseEnv = {
  PATH: `${process.env.HOME}/.local/bin:${process.env.PATH ?? ""}`,
  UV_CACHE_DIR: ".uv-cache",
  EMS_SOURCES: "mock",
  EMS_PRICES: "mock",
} as const;

// Validates the live EMS: API contract + the rendered dashboard (GOAL §6 visual gate). With auth on,
// every non-exempt /api/* request needs a bearer token — the "app" project supplies one so the
// existing specs run authenticated; "auth" runs the onboarding/login flow on its own fresh server.
export default defineConfig({
  testDir: "./e2e",
  timeout: 15000,
  use: { trace: "on-first-retry" },
  reporter: [["list"]],
  projects: [
    // Onboards the first admin on the app server (migrates EMS_WEB_TOKEN into an admin access token).
    { name: "setup", testMatch: /auth\.setup\.ts$/, use: { baseURL: APP_URL } },
    // Authenticated dashboard + API specs. The migrated admin token is sent as the Bearer on every
    // request (page fetches AND the `request` fixture), so the identity gate lets them through.
    {
      name: "app",
      testIgnore: [/auth\.spec\.ts$/, /auth\.setup\.ts$/],
      dependencies: ["setup"],
      use: {
        baseURL: APP_URL,
        extraHTTPHeaders: { Authorization: `Bearer ${E2E_ACCESS_TOKEN}` },
      },
    },
    // The auth flow itself (fresh-DB onboarding -> login -> logout) on its own tokenless server, so
    // its "no users yet" assertions hold and it is unaffected by the app server's onboarding.
    { name: "auth", testMatch: /auth\.spec\.ts$/, use: { baseURL: AUTH_URL } },
  ],
  webServer: [
    {
      command:
        "rm -f .e2e-data/app.sqlite* && mkdir -p .e2e-data && " +
        `uv run uvicorn ems.main:app --host 127.0.0.1 --port ${APP_PORT}`,
      cwd: repoRoot,
      url: `${APP_URL}/health/live`,
      reuseExistingServer: false,
      timeout: 60000,
      env: { ...baseEnv, EMS_DB_PATH: ".e2e-data/app.sqlite", EMS_WEB_TOKEN: E2E_ACCESS_TOKEN },
    },
    {
      command:
        "rm -f .e2e-data/auth.sqlite* && mkdir -p .e2e-data && " +
        `uv run uvicorn ems.main:app --host 127.0.0.1 --port ${AUTH_PORT}`,
      cwd: repoRoot,
      url: `${AUTH_URL}/health/live`,
      reuseExistingServer: false,
      timeout: 60000,
      env: { ...baseEnv, EMS_DB_PATH: ".e2e-data/auth.sqlite" },
    },
  ],
});
