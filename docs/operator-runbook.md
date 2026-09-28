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
| **Check whether the current plan matches measured battery behaviour** | `GET /api/plan-verification` — read-only comparison of planned intent, target SoC, and latest measured SoC/power. |
| **Interpret tariff warnings** | Review `tariff_warnings` in `/api/plan`, `/api/report`, or `/api/savings`; they identify missing or contradictory import/export fee assumptions. |
| **Check control-health incidents** | System page → *Control health* panel, or `GET /api/incidents` (rollup of command failures, cluster mismatches, fallbacks, reverts over the window). |
| **Enter/exit dry-run (watch-only)** | **Settings → Control & safety → "Watch only (no battery writes)"** (and/or "Let the system control the battery"). Save → **Apply & restart**. No daily `config.yaml` edit. The UI shows a large `DRY-RUN`/`LIVE` (Watching only / Controlling) badge **plus the primary cause** (demo/mock, config.yaml, Settings watch-only, unarmed, no live prices/devices, observing grace) so “Watching only” is not mistaken for a broken Settings save. **config.yaml `control.dry_run: true`** (or `dev.mode: mock`/`replay`) still always forces watch-only over the UI (#136). **Caution:** a restart in operational mode runs `shutdown_restore` → AUTO and can abort an active cheap-charge window — prefer flipping after the window ends. |
| **See yaml vs Settings vs apply** | See **Config authority** below (hot / restart / boot-only). Each Settings field is badged **active now** or **needs restart** / **restart_pending**. |
| **Run the capability probe again** | Restart `ems` (probe runs at startup) or hit the probe endpoint; review the logged service/entity surface. |
| **Run locally on a Mac/laptop for testing** | `docker compose -f docker-compose.dev.yml up` with `dev.mode: mock` — no HA/battery/GPU, `dry_run` forced; dashboard at `http://localhost:8080`. For UI work, `npm run dev` (Vite HMR) proxying to the backend. See `SPEC §11.6`. |

## Config authority (yaml defaults vs settings-store)

Effective config = **`config.yaml` defaults + runtime settings-store overlay** (`/data`). The UI edits the store; `config.yaml` is the file-level seed / floor for a few keys. **Saved ≠ live** for restart-tagged keys until Apply & restart (boot still runs the previous values).

Apply column: **hot** = on save (controller/plan picks it up); **restart** = connection / arming, read at next process start (`restart_pending` until then); **boot-only** = only from yaml/env at process start (no Settings twin, or Settings cannot override the floor).

### Control & arming (operator-critical)

| yaml / boot default | store key (Settings) | Apply | Notes |
|---|---|---|---|
| `control.dry_run` (default **true**) | `control.dry_run` | **restart** | Seeded from yaml on first boot. yaml / `dev.mode: mock\|replay` **always wins** over Settings (#136). Missing store ⇒ watch-only. |
| `dev.mode` (`live`\|`mock`\|`replay`) | — | **boot-only** | mock/replay force dry_run; no Settings twin. |
| — (no yaml twin) | `control.operational` | **restart** | Default **false** (unarmed). Needs Watch only OFF + live devices + live Tibber + Indevolt IP. |
| — | `connection.use_live_devices` / `use_live_prices` | **restart** | Live-prices gate (#126): mock prices never arm writes. |
| — | `meters.*_ip`, `battery.indevolt_*`, `prices.tibber_token` | **restart** | Device/service wiring. |
| — | `control.max_switches_per_day`, `min_dwell_seconds`, car-guard knobs, `grid_limit_w`, … | **hot** | Pushed onto the live mode controller on save. |

### Planner & strategy (replan path)

| yaml / related default | store key | Apply | Notes |
|---|---|---|---|
| strategy / arbitrage sample keys in `config.yaml` | `strategy.mode`, `strategy.*` | **hot** | Next plan / season pick uses the store. |
| arbitrage economics sample | `planner.*` (efficiency, wear, margins, slots, …) | **hot** | `/api/plan` recomputes; Settings shows plan-preview impact. |
| `planner.mode` (rule_based) | `planner.mode` | **hot** | ml/advisory greyed until M6; same §8.11 validator. |

Full key list: `docs/config-reference.md` + `GET /api/settings` `schema[].applies`.

## Rotate a token (Tibber / Solcast / HA / web)

1. Create the new token at the provider (Tibber, Solcast Toolkit, HA profile, or generate a new web token).
2. Update the **secret source** — Settings UI (Tibber / Solcast fields) or env / secrets file — **never** put tokens in `config.yaml` literals or logs. For Solcast also keep the rooftop resource id in Settings (or `SOLCAST_RESOURCE_ID` on first boot); Solcast fields only appear when the forecast provider is set to Solcast.
3. Restart the EMS so connection settings reload. Confirm via `/health/ready` and the relevant freshness indicator going green.
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
1. Stop the app (`docker compose stop ems` or `./scripts/uninstall.sh`). The scripts use LaunchAgent
   on macOS and a systemd user service on Linux.
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
