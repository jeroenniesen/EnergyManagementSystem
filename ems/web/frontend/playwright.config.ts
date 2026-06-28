import { fileURLToPath } from "node:url";
import path from "node:path";

import { defineConfig } from "@playwright/test";

// ems/web/frontend -> repo root (portable; no hardcoded absolute path).
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

// Validates the live EMS: API contract + the rendered dashboard (GOAL §6 visual gate).
// Assumes the FastAPI app (serving the built SPA from ems/web/static/dist) is already
// running at :8080. The caller starts it; reuseExistingServer keeps it simple.
export default defineConfig({
  testDir: "./e2e",
  timeout: 15000,
  use: {
    // Port 8099 for tests (8080 may be taken by another local app); the app's default stays 8080.
    baseURL: "http://127.0.0.1:8099",
    trace: "on-first-retry",
  },
  reporter: [["list"]],
  // Start the FastAPI app (serving the built SPA from ems/web/static/dist) for the test run.
  // HERMETIC: a fresh throwaway DB (rm'd first) via EMS_DB_PATH + forced mock sources/prices, so the
  // run never reads the operator's persisted settings, never touches live LAN devices, and AI stays
  // off (default). Without this, the app booted the operator's live/AI settings and e2e failed.
  webServer: {
    command:
      "rm -f .e2e-data/ems.sqlite* && mkdir -p .e2e-data && " +
      "uv run uvicorn ems.main:app --host 127.0.0.1 --port 8099",
    cwd: repoRoot,
    url: "http://127.0.0.1:8099/health/live",
    reuseExistingServer: false,
    timeout: 60000,
    // Repo-local uv cache so the webServer start is hermetic (no shared/sandboxed user cache).
    env: {
      PATH: `${process.env.HOME}/.local/bin:${process.env.PATH ?? ""}`,
      UV_CACHE_DIR: ".uv-cache",
      EMS_DB_PATH: ".e2e-data/ems.sqlite",
      EMS_SOURCES: "mock",
      EMS_PRICES: "mock",
    },
  },
});

