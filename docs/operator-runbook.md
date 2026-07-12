# Operator runbook

> Companion to `../SPEC.md` §11–§12. Practical "how do I…" procedures for running the EMS on the Pi. Assumes the single-host Docker Compose layout (HA Container + Mosquitto + EMS).

## Quick reference

| I want to… | Do this |
|---|---|
| **Disable the EMS entirely** | `docker compose stop ems` — on graceful stop the EMS restores the battery's safe vendor mode (original mode, or `AUTO`); it then self-consumes as before, EMS-free. Nothing else is affected. |
| **Force `AUTO` for N hours** | Web UI → *Manual override* → "Force AUTO for 6 h" (sets an **expiring** override). Or HA `select.ems_mode_override` → `AUTO`. Or set `strategy.mode: manual` + an AUTO pin. |
| **Pin a specific mode** | UI manual override with an expiry, or HA `select.ems_mode_override`. The override lapses automatically at expiry. |
| **Inspect the last decision** | UI dashboard top line (current mode + reason + "why not"), or `GET /api/status` and `GET /api/plan`. Logs show each cycle's decision. |
| **See why it's NOT charging/discharging** | UI shows the no-action reason; `GET /api/status` includes it (e.g. "no-trade day: net benefit −€0.02/kWh"). |
| **Check data freshness** | UI per-source freshness indicators, or `GET /api/freshness`. |
| **Download plan / measurements** | UI export buttons, or `GET /api/export/plan` / `GET /api/export/measurements`. |
| **Export the full history (for analysis or a health check)** | System page → **"Download export package (ZIP)"**, or `GET /api/export/package?days=N`. One ZIP: all history CSVs (raw, derived, prices, forecast vs. actual, plan history, daily finance, gas, audit) + `manifest.json` + `validation_summary.txt`. **Redacted** — allowlisted config keys only, no tokens/IPs/location — so it's safe to share. Read the **"Solar forecast skill"** section (bias/MAE/band-coverage) and the **incident rollup** in the summary; missing finance days are backfilled into the window on export. |
| **Check control-health incidents** | System page → *Control health* panel, or `GET /api/incidents` (rollup of command failures, cluster mismatches, fallbacks, reverts over the window). |
| **Enter/exit dry-run** | `control.dry_run: true` in `config.yaml` (logs decisions, no writes) → restart `ems`. The UI shows a large `DRY-RUN`/`LIVE` badge. |
| **Run the capability probe again** | Restart `ems` (probe runs at startup) or hit the probe endpoint; review the logged service/entity surface. |
| **Run locally on a Mac/laptop for testing** | `docker compose -f docker-compose.dev.yml up` with `dev.mode: mock` — no HA/battery/GPU, `dry_run` forced; dashboard at `http://localhost:8080`. For UI work, `npm run dev` (Vite HMR) proxying to the backend. See `SPEC §11.6`. |

## Rotate a token (Tibber / Solcast / HA / web)

1. Create the new token at the provider (Tibber, Solcast, HA profile, or generate a new web token).
2. Update the **secret source** (env var / secrets file) — **never** put tokens in `config.yaml` literals, the settings DB, or logs.
3. `docker compose up -d ems` to reload. Confirm via `/health/ready` and the relevant freshness indicator going green.
4. Revoke the old token at the provider.

## Back up & restore

**Automatic (B-52):** the app snapshots its own DB daily — an online `VACUUM INTO` copy at
`<db_dir>/backups/ems-YYYYMMDD.sqlite`, keeping the newest `history.backup_keep` (default 7,
`0` disables). Check it ran: `GET /api/diagnostics` → `storage.backup` (last time/size/ok).
These snapshots live on the **same disk** — still copy the newest one off-machine on your own
schedule, plus:
- `config.yaml`
- a note of **where** each secret lives (env/secret file path) — **not** the secret values

**Restore:**
1. Stop the app (`docker compose stop ems`, or `make uninstall` / `launchctl` on a Mac install).
2. Copy a snapshot from `backups/ems-YYYYMMDD.sqlite` over `ems.sqlite` (remove any leftover
   `ems.sqlite-wal`/`-shm` files); restore `config.yaml`.
3. Ensure secrets are present in their env/secret source.
4. Start the app; verify `/health/ready`, freshness, and that the last plan loads.

## Health & maintenance

- **Liveness/readiness:** `GET /health/live` (process up), `GET /health/ready` (config loaded, HA reachable or explicitly degraded, DB writable). The Docker `healthcheck` polls `/health/ready`.
- **NTP:** the Pi's clock **must** be synced (price/charge windows are time-critical). `health.ntp_check` alerts on drift; fix with the OS time-sync service.
- **DB growth:** a daily maintenance task purges samples older than `history.retention_days` (default 90; 0 = keep forever) from both sample tables atomically, then truncates the WAL and runs an incremental vacuum to reclaim space. Timestamp indexes keep the story/forecast windows fast as the DB ages. DB/WAL size + sample row counts are on `GET /api/diagnostics` (`storage`). If the disk fills, free space then restart.
- **Logs:** when `EMS_LOG_FILE` is set (the Mac LaunchAgent install sets it to `ems/data/server.log`), app logs go to a **size-rotated** file (`EMS_LOG_MAX_MB`×`EMS_LOG_BACKUPS`, default 5 MB × 5); per-request access logging is off. `server-crash.log` holds only launchd start/crash output. Tokens are redacted from logs and debug dumps.
- **Graceful shutdown:** on a clean stop (`docker compose stop`, a launchd stop/restart, or SIGTERM) **in operational mode**, the EMS issues **one final safe-restore command** — the battery's captured original vendor mode, or `AUTO` if unknown, and never a forced charge/discharge — so it never stops mid-forced-charge/discharge. It's bounded (won't hang shutdown on a slow/offline device) and audited (`shutdown_restore`). In dry-run nothing is written.
- **Recorder health:** if sampling stops (full disk, DB lock, dead device), `GET /api/diagnostics` (`recorder`) shows `consecutive_failures`, `last_success_at`, and `last_error` — so the cause is visible, not just inferred from stale data.

## When something looks wrong

1. Check the **freshness indicators** and **alerts** first (stale prices/forecast, battery write failed, fallback active, NTP).
2. If `FALLBACK ACTIVE`, the battery is in `AUTO` — safe but not optimising. Find the stale input via `/api/freshness`.
3. Check `docker compose logs ems` for the decision trace and any retry→AUTO recovery.
4. To stop all automation immediately: `docker compose stop ems` (reverts to battery `AUTO`).

See `failure-modes.md` for the full detection → safe-behaviour → recovery table.
