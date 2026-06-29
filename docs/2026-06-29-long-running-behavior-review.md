# Long-Running Behavior Review - 2026-06-29

## Scope

This review looks at the application as a 24/7 service: storage growth, request volume, external API usage, device polling, restart behavior, shutdown behavior, and failure modes that only show up after days or weeks.

I reviewed the current worktree as authoritative. The code already includes useful protections, especially Tibber and Forecast.Solar TTL caches, persistent cache warm-start, bounded AI explanation memory cache, switch dwell/cap logic, and dashboard polling reduced to 10 seconds. The remaining findings focus on what can still fail or degrade over long runtimes.

## Iteration 1 - Research: Runtime Map

### Always-running paths

1. **Application startup**
   - `ems/main.py` builds one shared SQLite database, history/settings/audit/cache stores, source wiring, recorder, controller, and FastAPI app.
   - `ems/web/api.py` lifespan initializes stores, loads settings, probes the battery capability once, takes one startup recorder sample, then starts background tasks.

2. **Recorder loop**
   - `Recorder.run()` reads the source every `cycle_seconds`.
   - `config.yaml` currently sets `control.cycle_seconds: 5` for dev-like behavior, while comments say the production default is 300 seconds.
   - Every successful sample writes one row to `raw_samples` and one row to `derived_samples`.

3. **Control loop**
   - Runs only when not `dry_run`.
   - It advances lifecycle readiness, reads battery current mode, resolves the effective intent, and applies mode changes through `ModeController`.
   - Mode changes are gated by ownership state, idempotency, minimum dwell, and daily switch cap.

4. **Audit and validation loops**
   - Decision audit loop runs every `control_cycle_seconds` in any mode and appends only when desired battery mode changes.
   - AI validation loop wakes every configured interval, or every 6 hours when disabled, and purges expired persistent cache rows.

5. **Dashboard polling**
   - The dashboard polls every 10 seconds while visible.
   - One browser tab calls `/api/status`, `/api/freshness`, `/api/energy-story`, `/api/strategy`, `/api/battery`, `/api/decision`, `/api/alerts`, `/api/savings`, and `/api/charge-need`.
   - System and override views have their own 10 second polling loops.

### Existing protections

1. **Tibber caching**
   - `TibberPriceSource` has a 15 minute success TTL, 60 second retry TTL, last-good fallback, persistent warm-start, and a single-flight lock.
   - This is good enough to avoid hammering Tibber under normal dashboard polling.

2. **Forecast.Solar caching**
   - `ForecastSolarSource` has a 30 minute TTL, persistent warm-start, model fallback, and a single-flight lock.
   - This is appropriate for the keyless free API and its low rate limit.

3. **Cache table housekeeping**
   - `CacheStore.purge_expired()` exists.
   - Expired cache rows are purged at boot and in the AI validation loop.

4. **Battery write amplification guardrails**
   - `ModeController.decide()` counts failed writes toward dwell and the daily switch cap.
   - This is important because a flaky device should not receive a write attempt every control cycle.

5. **Frontend view gating**
   - Dashboard polling stops when the user switches away from the dashboard.
   - System view and override view poll only when mounted.

## Iteration 2 - Processing: Long-Run Risks

### P0 - Unbounded time-series history

**Finding:** `HistoryStore` is append-only and has no retention, purge, index maintenance, vacuum, checkpoint, or backup mechanism implemented.

Evidence:
- `ems/storage/history.py` creates `raw_samples` and `derived_samples`, then only inserts and reads recent rows.
- `record()` inserts one raw and one derived row for every recorder cycle.
- `docs/operator-runbook.md` and `SPEC.md` mention `retention_days`, `vacuum_on_start`, and DB maintenance, but there is no implementation in `HistoryStore`.

Impact:
- At 300 seconds, the app writes 288 rows/day total across both sample tables.
- At the current checked-in `cycle_seconds: 5`, it writes 34,560 rows/day total across both sample tables.
- A Mac or Pi can tolerate this for a while, but it is still an avoidable unbounded growth path.
- More importantly, the user-facing story and forecast paths query by timestamp against tables with no timestamp index. The longer the DB lives, the more expensive recent-window queries can become.

Recommendation:
- Add `history.retention_days` and enforce it in a daily maintenance task.
- Add indexes on `raw_samples(ts)` and `derived_samples(ts)`.
- Add periodic `PRAGMA wal_checkpoint(TRUNCATE)` and controlled `VACUUM` or `auto_vacuum=INCREMENTAL`.
- Add tests proving old rows are deleted from both raw and derived tables atomically.
- Make install/default production cadence 300 seconds unless the user explicitly chooses development sampling.

### P0 - Checked-in sampling cadence is unsafe for 24/7 installs

**Finding:** The checked-in config uses a 5 second sample interval, even though the comment says production should be 300 seconds.

Evidence:
- `config.yaml` has `control.cycle_seconds: 5`.
- The Mac installer uses the repository config as-is.

Impact:
- Long-running installs created by the one-command Mac installer may record every 5 seconds indefinitely.
- This increases SQLite write rate, WAL churn, log volume under failures, and HomeWizard/Indevolt read volume.
- It also makes the decision audit loop wake every 5 seconds, even though mode decisions should change rarely.

Recommendation:
- Change the shipped default to 300 seconds.
- Add a separate dev override, for example `config.dev.yaml` or `EMS_CYCLE_SECONDS=5` for local development.
- In the UI/System page, warn when `cycle_seconds < 60` while live devices are enabled.

### P0 - Live-read coalescing is not single-flight

**Finding:** `_current_sample()` and `_current_towers()` cache successful reads for 30 seconds, but they do not serialize concurrent cache misses.

Evidence:
- `_sample_cache` and `_tower_cache` are plain dictionaries in `ems/web/api.py`.
- On a cold start or expired cache, multiple concurrent dashboard endpoints can all observe a stale cache and call `source.read()` or `reader.read_towers()`.
- `test_read_coalescing.py` verifies sequential requests inside the cache window, but not concurrent requests landing at cache expiry.

Impact:
- The app comments explicitly say read volume can knock an Indevolt tower offline.
- The current cache helps steady state, but the highest-risk moments are cold start, cache expiry, multiple browser tabs, and slow hardware responses. Those are exactly the cases that need single-flight behavior.

Recommendation:
- Add a process-local lock around `_current_sample()` and `_current_towers()` with double-checked cache logic.
- If the function is used from async endpoints, do not hold the event loop while waiting for live hardware. Use a background sampler or `asyncio.to_thread`.
- Add a concurrent test with 10-20 simultaneous requests to hot endpoints and assert one source read per window.

### P0 - Blocking network I/O can run in async request handlers

**Finding:** Several async endpoints call synchronous helpers that can perform network I/O directly in the event loop.

Evidence:
- `/api/decision` is async and calls `_car_charging()`, `_effective_intent()`, `_current_plan()`, `_current_soc()`, `_readiness()`, and `controller.preview()` directly.
- `_current_plan()` calls `price_source.slots()`, `solar_forecast.slots()`, and `_current_soc()`.
- `_current_soc()` can call `_current_sample()`, which can call `source.read()`.
- `/api/energy-story` and `/api/energy-forecast` call `_forward_projection()`, which calls `_current_plan()` and `solar_forecast.slots()`.

Impact:
- If a live meter, Indevolt read, Tibber refresh, or Forecast.Solar refresh is slow, the FastAPI event loop can stall.
- A single slow local device could make unrelated requests feel frozen.
- Under multiple clients, this can cascade into timeouts and overlapping retry behavior.

Recommendation:
- Choose one of these designs:
  - Preferred: background data refreshers own all live network I/O and API endpoints read snapshots.
  - Acceptable: every potentially blocking source call from async endpoints goes through `asyncio.to_thread`.
- Add a test source whose `read()` sleeps and verify async endpoints do not block unrelated `/health/live` requests.

### P0 - Shutdown does not restore the battery to safe vendor mode

**Finding:** The docs and comments promise graceful shutdown safe restore, but lifespan shutdown only stops background tasks.

Evidence:
- The app lifespan `finally` sets the stop event and awaits tasks.
- There is no final `AUTO` or original vendor mode restore call in shutdown.
- `docs/operator-runbook.md` says stopping EMS restores the battery's safe vendor mode.
- `SPEC.md` also calls out restoring original vendor mode on graceful shutdown.

Impact:
- If operational mode has put the battery into a forced charge, forced hold, or forced discharge mode, a graceful service stop may leave it there.
- This is one of the highest-risk long-running behaviors because it appears during upgrades, crashes, Mac logout/reboot, launchd restarts, and manual restarts.

Recommendation:
- On graceful shutdown in operational mode, issue one bounded-time safe restore:
  - prefer captured `original_vendor_mode` if known and safe;
  - otherwise command vendor `AUTO`.
- Audit the result.
- Do not block shutdown indefinitely. Use a short timeout and log failure.
- Add tests for shutdown restore with a mock operational driver.

### P1 - Dashboard poll fan-out causes repeated plan/projection work

**Finding:** One visible dashboard tab polls many endpoints every 10 seconds. Device reads are partly coalesced, but plan/projection computation is repeated across endpoints.

Evidence:
- `App.tsx` polls nine dashboard endpoints.
- `/api/decision`, `/api/alerts`, `/api/savings`, `/api/charge-need`, `/api/energy-story`, `/api/strategy`, and `/api/status` each rebuild or touch overlapping state.
- `_current_plan()` is intentionally the common helper, but it is still recomputed per endpoint call.

Impact:
- CPU cost is probably fine today, but this design scales poorly with multiple tabs and future heavier logic such as ML/advisory planners.
- It also increases the chance of inconsistent snapshots across cards because each endpoint computes "now" separately.

Recommendation:
- Add a short-lived server-side dashboard snapshot cache, for example 5-15 seconds.
- Or add a single `/api/dashboard` endpoint that returns status, freshness, strategy, decision, alerts, savings, charge need, and story summary from one consistent snapshot.
- Keep detailed endpoints for drill-down and exports.

### P1 - Operational preview can repeatedly read battery mode

**Finding:** In non-dry-run operational mode, read-only previews can still call `driver.current_mode()`.

Evidence:
- `ModeController._gate()` returns early in dry-run, but in live control it calls `self.driver.current_mode()` for idempotency.
- `/api/decision` calls `controller.preview()`.
- `/api/alerts` also previews the decision outcome.

Impact:
- In operational mode, a dashboard tab can add mode-read traffic every 10 seconds, independent of the recorder and control loop.
- If the current-mode read is slow or flaky, it can affect UI responsiveness and local device load.

Recommendation:
- Cache current battery mode for a short window, or move current-mode observation into the control/background read path.
- Make preview use the latest observed mode when available instead of reading hardware in request path.
- Keep the control loop's own pre-write confirmation stricter than the UI preview.

### P1 - Recorder failures are too silent for long-running operation

**Finding:** The recorder loop suppresses all exceptions during periodic recording.

Evidence:
- `Recorder.run()` catches `Exception` and does `pass`.
- That keeps the loop alive, which is good, but a full DB or permanently failing device may remain invisible except through stale freshness.

Impact:
- If SQLite becomes unwritable, samples stop and the app may only show stale data after the freshness window.
- The operator may not know that disk space or database locking is the root cause.

Recommendation:
- Add throttled logging for recorder failures.
- Track `last_recorder_error`, `last_success_at`, and consecutive failure count.
- Surface DB write failures on `/api/diagnostics` and the System page.

### P1 - Server logs are unbounded in the Mac LaunchAgent install

**Finding:** The Mac installer routes both stdout and stderr to `ems/data/server.log`, but no rotation is configured.

Evidence:
- `scripts/install.sh` writes `StandardOutPath` and `StandardErrorPath` to the same file.
- I did not find a rotation mechanism in the installer.
- README says logs are at `ems/data/server.log`.

Impact:
- Under normal operation this may stay small.
- Under repeated device timeouts, Tibber failures, Forecast.Solar rate limits, or debug logging, it can grow without bound.

Recommendation:
- Add simple log rotation to the LaunchAgent setup:
  - either use Python logging with `RotatingFileHandler`;
  - or start through a wrapper that rotates `server.log` by size.
- Keep at most N files or N MB.
- Add a System page check for log file size.

### P1 - Docs and runtime behavior have drifted

**Finding:** Several docs still describe behavior that is not implemented or no longer exactly true.

Examples:
- `docs/operator-runbook.md` says retention and vacuum exist.
- `docs/operator-runbook.md` says graceful stop restores safe vendor mode.
- `docs/live-integration.md` still describes live control as impossible in the shipped wiring, while current wiring can arm the driver when `control.operational` is enabled with live devices and a configured battery IP.

Impact:
- Operators may trust safety or maintenance behavior that is not actually present.
- This is especially risky for long-running control software where the runbook is used during incidents.

Recommendation:
- Update docs after implementing the missing behavior, or mark them as planned until implemented.
- Add a "runtime guarantees" checklist to `/api/diagnostics` so docs can be verified from the app itself.

### P2 - Cache table is bounded by TTL, but not by count/size

**Finding:** `CacheStore` purges expired rows, but there is no maximum row count or maximum value size.

Evidence:
- `CacheStore` has TTL expiry and `purge_expired()`.
- AI explanation cache keys are hashed and bounded by TTL, Tibber and Forecast.Solar use stable keys.

Impact:
- Current usage is low risk because external source keys are stable and AI explanation memory is bounded.
- A future feature that stores many distinct cache keys could grow the cache until TTL expiry.

Recommendation:
- Add optional `max_rows` and `max_value_bytes` enforcement.
- Keep current stable-key behavior.
- Add diagnostics for cache row count and DB size.

### P2 - SQLite connection-per-operation is simple but may become noisy

**Finding:** Every store operation opens and closes a SQLite connection.

Impact:
- This is acceptable at current rates, especially with WAL and busy timeouts.
- It may become noisy with nine dashboard endpoints, multiple tabs, 5 second recorder cadence, audit loop, and exports.

Recommendation:
- Do not prematurely add pooling.
- First reduce polling fan-out and sampling cadence.
- If DB lock warnings appear, then consider an app-scoped async connection for hot read paths.

## Iteration 3 - Validation: What Is Already Good vs. What Must Change

### Already good enough

1. **Tibber request volume**
   - 15 minute TTL, retry backoff, persistent warm-start, last-good fallback, and single-flight are appropriate.

2. **Forecast.Solar request volume**
   - 30 minute TTL, persistent warm-start, model fallback, and single-flight are appropriate for the free API.

3. **AI explanation token spend**
   - Deterministic/template mode is default.
   - External LLM is opt-in.
   - Explanations use persistent TTL cache and bounded in-memory cache.

4. **Battery write amplification**
   - Failed writes count toward dwell and daily switch cap.
   - This avoids repeated write attempts against a failing battery.

5. **Frontend polling is partially constrained**
   - Polling is view-gated and uses 10 seconds rather than 5 seconds.
   - This is acceptable if the server consolidates snapshot work and hardware reads.

### Not yet good enough for unattended 24/7 operation

1. History retention and vacuum are missing.
2. The installed default sampling cadence can be too aggressive.
3. Live hardware coalescing needs single-flight and async-safe execution.
4. Shutdown safe restore is missing.
5. Operational preview should not read hardware on every dashboard poll.
6. Long-running diagnostics need to expose recorder failures, DB size, WAL size, log size, cache stats, and request/device read counters.

## Prioritized Implementation Backlog

### P0 - Implement before trusting unattended live operation

1. **Set production sampling cadence to 300 seconds**
   - Keep 5 seconds only for foreground/dev mode.
   - Add a UI warning for live devices with `cycle_seconds < 60`.

2. **Add history retention and DB maintenance**
   - Implement `HistoryStore.purge_older_than(cutoff)`.
   - Add `history.retention_days`.
   - Add indexes on timestamp columns.
   - Add daily maintenance task.
   - Add WAL checkpoint and controlled vacuum.

3. **Make live-read coalescing single-flight**
   - Lock `_current_sample()` and `_current_towers()` cache misses.
   - Add concurrent tests, not only sequential tests.

4. **Remove network I/O from async request path**
   - Prefer background snapshots.
   - Otherwise wrap blocking source reads in `asyncio.to_thread`.

5. **Add graceful shutdown safe restore**
   - In operational mode only.
   - Restore original vendor mode or `AUTO`.
   - Use a short timeout and audit/log the result.

### P1 - Implement next

1. **Add `/api/dashboard` snapshot or short-lived plan cache**
   - One coherent snapshot per poll.
   - Reduce repeated plan/projection computation.

2. **Cache operational `current_mode` reads**
   - Request-path previews should use the last observed mode.
   - Control-path decisions can still perform stricter direct checks.

3. **Add long-run diagnostics**
   - DB file size.
   - WAL file size.
   - sample row counts.
   - audit row count.
   - cache row count.
   - log file size.
   - recorder consecutive failures.
   - last successful sample.
   - device read counters per minute.
   - external API refresh counters and last error.

4. **Add log rotation for Mac LaunchAgent**
   - Size-based rotation is enough.
   - Surface log size in diagnostics.

5. **Make docs match runtime guarantees**
   - Do not claim retention, vacuum, or shutdown restore until implemented.

### P2 - Useful hardening

1. **Add cache max row/value limits**
   - Low risk today, useful before more cached features appear.

2. **Add an operator maintenance endpoint**
   - Read-only diagnostics by default.
   - Auth-gated actions for checkpoint/vacuum/export backup.

3. **Add soak tests**
   - Simulate 7 days at 5 second and 300 second cadence.
   - Simulate multiple browser tabs.
   - Simulate Forecast.Solar/Tibber failures.
   - Simulate a slow/offline Indevolt tower.

4. **Add fault-injection tests for DB full/unwritable**
   - Confirm recorder degrades visibly.
   - Confirm control does not crash.

5. **Add request/device-read telemetry**
   - Count real HomeWizard reads, tower reads, mode reads, Tibber calls, Forecast.Solar calls, LLM calls.
   - Use this to prove the app is not flooding devices.

## Recommended Soak-Test Plan

1. **Fast local soak**
   - Run mock mode with `cycle_seconds=1` for 30 minutes against a temp DB.
   - Assert DB row counts match expected rows and memory does not grow unexpectedly.

2. **Simulated long soak**
   - Use an injectable clock to simulate 7-30 days of recorder writes and maintenance.
   - Assert retention keeps rows within the configured window.

3. **Concurrent dashboard soak**
   - Simulate 3-5 browser tabs calling dashboard endpoints every 10 seconds.
   - Assert live source reads stay at one per coalescing window.

4. **Slow-device soak**
   - Make HomeWizard and Indevolt reads sleep near timeout.
   - Assert `/health/live` and unrelated API requests remain responsive.

5. **External API failure soak**
   - Tibber returns 429 for one hour.
   - Forecast.Solar returns timeout for one hour.
   - Assert no hammering, no empty plan collapse when last-good data exists, and no log flood.

## Resolution — implementation pass (2026-06-29)

Every finding was triaged against the current code and either fixed (with tests) or consciously
deferred with a rationale. Validated: `ruff` clean, full pytest suite green, SPA build, e2e green.

### P0 — all fixed
1. **Unbounded history** → timestamp indexes on both tables; `HistoryStore.purge_older_than()` +
   `history.retention_days` (default 90); a daily maintenance task purges old rows and runs WAL
   checkpoint + incremental vacuum. Tests: atomic two-table purge, maintain/db_stats.
2. **Sampling cadence** → shipped default is now **300 s**; `EMS_CYCLE_SECONDS` is the dev fast-sample
   override. (Dashboard tiles stay live via the 30 s coalesced read, independent of cadence.)
3. **Single-flight live reads** → `threading.Lock` + double-check on `_current_sample` /
   `_current_towers`. Test: 20 concurrent cold requests → exactly one hardware read.
4. **No blocking network I/O in async handlers** → `/api/decision`, `_forward_projection`
   (story/forecast) and `/api/diagnostics` run their blocking source/price/forecast work in
   `asyncio.to_thread`.
5. **Graceful shutdown safe restore** → on a clean stop in operational mode, the battery is handed
   back to its safe vendor mode (original, else `AUTO`; never a forced energy mode); bounded +
   audited. Tests cover restore, dry-run no-op, nothing-to-undo, and forced-original fallback.

### P1
- **Operational preview mode-reads** (fixed): coalesced `_current_mode` + `preview(observed_mode=)`,
  so a dashboard poll no longer reads battery mode each cycle; `decide()` still reads fresh.
- **Recorder failure visibility** (fixed): `consecutive_failures` / `last_error` / `last_success_at`,
  throttled logging, surfaced on `/api/diagnostics` (`recorder`). Fault-injection test included.
- **Unbounded Mac logs** (fixed): `EMS_LOG_FILE` → size-rotated `RotatingFileHandler`; LaunchAgent
  runs with `--no-access-log` and a separate tiny crash log.
- **Long-run diagnostics** (fixed): DB/WAL bytes + sample row counts (`storage`) and recorder health
  on `/api/diagnostics`.
- **Docs drift** (fixed): `live-integration.md` now describes operational arming correctly;
  `operator-runbook.md` retention/maintenance/shutdown/log claims match the implementation.
- **Dashboard fan-out snapshot** (deferred): an optimization, not a long-run risk — repeated work is
  CPU only (no growth, no device load) and device reads are already coalesced + single-flight. Worth
  revisiting if multiple tabs or heavier ML planners land; tracked, not blocking.

### P2
- **DB-full fault injection** (fixed): recorder survives a failing store and surfaces the failure.
- **Cache max rows/size**, **connection pooling**, **request/device telemetry counters**, **full
  multi-day soak harness** (deferred): low value today (stable cache keys + TTL; the review itself
  says not to pool prematurely). Concurrent + retention + fault-injection tests cover the core soak
  concerns; the rest are revisited only if a real symptom appears.

## Bottom Line

The application has improved substantially on API caching and battery write guardrails, but I would not yet call it safe for unattended long-term live control. The critical missing pieces are operational rather than algorithmic: bounded storage, production sampling defaults, single-flight hardware reads, no blocking network I/O in async request paths, and graceful shutdown restore.

Implement the P0 items first. After that, run the soak tests above before enabling operational mode for multiple days.
