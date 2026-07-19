# Design — Control/API performance budgets (B-80)

*Brainstormed 2026-07-18. Owner: Jeroen. Feeds a `writing-plans` implementation plan.*
*Backlog: B-80 (E-09 ISO 25010 quality engineering, P1).*

## 1. Goal & context

Give EMS a written-down, measurable, test-enforced performance contract so we
can prove the system stays responsive in real-home conditions — and so a
regression that doubles the control cycle duration gets caught before it
ships, not after a user notices a sluggish dashboard.

This is **documentation + timing instrumentation + two tests + a local
gate**. It is not a monitoring system, not Prometheus, not request
rate-limiting.

**Out of scope (explicit):** Prometheus / OpenTelemetry / external monitoring;
rate-limiting incoming requests; request cancellation on over-budget; CI gate;
Pi-specific budget enforcement.

**North-star constraints (unchanged):** local-first, fail-safe ("never worse
than no EMS"), single battery writer, dry-run respected, no behavior change
to the control tick itself.

### Hard invariants to preserve

1. **The pure-ASGI `_AccessMiddleware` rule still applies.** The new
   perf-timing middleware must also be pure ASGI, never `@app.middleware` /
   `BaseHTTPMiddleware` (the latter starves the override control cycle).
2. **The control tick logic does not change.** The wrapper measures; the
   over-budget behavior lives outside `control_tick()` itself, in
   `run_cycle()`.
3. **Dry-run is preserved.** In dry-run, an over-budget cycle logs and
   audits but does not call `driver.apply(mode=AUTO)` (there's no live
   write to suppress).
4. **Lifecycle startup grace is preserved.** During the grace window the
   wrapper measures and audits overruns, but does not force AUTO (the
   lifecycle already gates writes).
5. **Single battery writer.** The wrapper's force-AUTO path goes through
   `driver.apply()` — the same single seam.

## 2. Budgets

A single source of truth: `PERF_BUDGETS` constant in `ems/perf.py`,
mirrored in `docs/perf-budgets.md`.

| Name | Tier | Budget | Where it applies |
|---|---|---|---|
| `api.hot.p95` | H | **500 ms** | 11 dashboard-10s routes + override poll |
| `api.interactive.p95` | I | **1 s** | on-mount routes (`/api/settings`, `/api/cars`, `/api/forecast`, `/api/auth/me`, etc.) |
| `api.batch.p95` | B | **8 s** | `/api/export/package`, `/api/report?period=year`, `/api/counterfactual`, `/api/digest` |
| `control.cycle.wall` | — | **20 s** (hard) | one `_run_control_cycle()` |
| `store.history.read.p95` | — | **100 ms** | history-store read transactions |
| `store.history.write.p95` | — | **500 ms** | history-store write transactions |
| `replay.run.wall` | — | **30 s** | `ems.replay.run_replay(...)` |
| `report.build.wall` | — | **30 s** | `ems.reporting.build_report(...)` year/week assembly |
| `memory.rss.peak` | — | **350 MB** | process RSS ceiling, sampled every 60 s |

### API tier classification

Small explicit table in `ems/web/perf_middleware.py`. Path template (not raw
query string) is used for tier lookup:

```python
HOT_API_PREFIXES = (
    "/api/status", "/api/freshness", "/api/energy-story", "/api/battery-plan",
    "/api/strategy", "/api/battery", "/api/decision", "/api/alerts",
    "/api/finance", "/api/charge-need", "/api/car/plan", "/api/override",
)
BATCH_API_PREFIXES = (
    "/api/export/package", "/api/counterfactual", "/api/digest",
    "/api/car/sessions", "/api/advisor/ev-charge",
)
# everything else under /api/ → I
```

## 3. Over-budget behavior

| Where | What happens |
|---|---|
| API request over tier p95 | Log WARN with `name=api.<tier>, path, duration_ms, status`; request completes normally; sample pushed to registry. **No request is cancelled** — a slow request still returns its data. |
| Control cycle over 20 s | Audit-log `control.overrun` event with duration and which phase dominated (sense / decide / write / audit). If `not dry_run` and lifecycle is past grace: `driver.apply(mode=AUTO)` *before* returning. If dry-run or during grace: log only, do not write. |
| SQLite txn over budget | Log WARN with store name + duration. No behavior change. |
| Replay / report over 30 s | Log WARN; no behavior change (these are on-demand, not on the control path). |
| RSS over 350 MB | Log WARN once per minute; expose in `/api/diagnostics.perf`. |

The control-cycle force-AUTO path uses the same `driver.apply()` seam as the
normal write path — single battery writer preserved. The audit event includes
the original intended mode so the *why* of the overrun is visible.

## 4. Architecture

Five small instrumentation seams, each isolated.

### 4.1 `ems/perf.py` (new, ~80 LOC)

Pure timing primitives, no I/O.

- `@timed(name)` decorator (sync) and `@atimed(name)` (async).
- `timed(name)` async context manager.
- Both push `(name, duration_ms, ts)` into a singleton `Registry`.
- `Registry` keeps a per-name in-memory ring buffer (last 1000 samples).
- `Registry.recent(name) -> list[Sample]` and
  `Registry.summarize(name) -> {p50, p95, p99, max, n, over_budget_count}`.
- `PERF_BUDGETS` constant (the table above, with tier + threshold_ms).

### 4.2 `ems/web/perf_middleware.py` (new, pure ASGI)

Sits **after** `_AccessMiddleware`, wraps every `/api/` request. Records
`method + path_template + duration_ms + status`. Tier classification by
path-prefix dict (HOT_API_PREFIXES / BATCH_API_PREFIXES, default I).
Over-budget → log WARN; does not cancel the request. Uses path template
(via route lookup), not raw query string, so `/api/report?period=year` is
distinct from `/api/report?period=day` in summaries.

Pure ASGI — same constraint as `_AccessMiddleware`. No
`@app.middleware` / `BaseHTTPMiddleware`.

### 4.3 `ems/control/service.py::run_cycle()` wrapper

Two layers around the existing `control_tick(now)` call:

1. **`asyncio.wait_for(..., timeout=20)`** — hard wall-clock deadline.
   This is the actual safety mechanism: a tick that hangs (deadlock,
   infinite loop, blocked device read) is cancelled at 20 s, raising
   `asyncio.TimeoutError`. The current code has no such deadline
   (confirmed in exploration: "If the tick raises, the exception is
   logged and the function returns" — a hung tick would simply be
   retried on the next 60 s tick). This slice adds the deadline.

2. **`timed("control.cycle")` context manager** — pushes a sample into
   the registry for diagnostics. Always runs (whether the tick
   completed normally, raised, or timed out).

Behavior after the tick ends:

| Outcome | Auto-write | Audit event |
|---|---|---|
| `duration ≤ 20 s`, no exception | no | none |
| `duration > 20 s`, no exception (slow but returned) | `driver.apply(mode=AUTO)` if `not dry_run` and lifecycle past grace | `control.overrun` with `duration_ms` |
| `asyncio.TimeoutError` (hung tick) | same as above | same, with `reason: "timeout"` |
| Other exception raised by tick | no | `control.error` (existing behavior) |
| Dry-run OR lifecycle in grace | no | `control.overrun` log + audit only — no driver write |

The audit event shape:

```json
{
  "ts": "...",
  "category": "control",
  "summary": "control.overrun",
  "detail": {
    "duration_ms": 21450,
    "reason": "timeout" | "duration",
    "intended_mode": "GRID_CHARGE" | null,
    "phase": "decide" | null
  }
}
```

`intended_mode` is the mode the tick reached in `decide()` (null if
the tick never returned). `phase` is the slow phase, derived from the
per-phase timing samples that **slice 4 adds** at the sense/decide/
write/audit boundaries of `control_tick()` itself. If those samples
aren't pushed (e.g. the tick timed out before reaching any of them),
`phase` is null. The existing tick logic is unchanged — only thin
`timed("control.<phase>")` push points are added.

The tick logic itself is otherwise unchanged. The wrapper is the only
new code path; the inner tick does not know it is being measured.

### 4.4 SQLite stores

Add `timed("store.<name>.<op>")` wrappers in each store's hot path:

- `ems/storage/history.py` — read/write transactions.
- `ems/storage/settings.py` — read/write.
- `ems/storage/audit.py` — append.
- `ems/storage/cache.py` — get/set (today opens a new sync sqlite3 per
  call; this is the worst offender on the synchronous SQLite side).
- `ems/storage/control_state.py` — read/write.

No SQL changes, no locking changes — measurement only. The wrappers are
a few lines each, applied as `with timed("store.<name>.<op>"):` blocks
immediately inside the existing `async with self._lock:` critical
sections (so lock-acquire wait is included in the duration, which is
the relevant signal for SQLite contention). `CacheStore` is a
candidate for the largest absolute win because of its per-call
connection cost.

### 4.5 `ems/replay.py` and `ems/reporting.py`

Wrap `run_replay(...)` body with `timed("replay.run")`. Wrap the year/week
report assembly (`build_report`, `build_series`, `build_daily_flows`)
with `timed("report.build")`. On-demand batch workloads; WARN-only
over-budget behavior.

### 4.6 `/api/diagnostics` extension

Add a `perf` block alongside the existing `recorder_health`:

```json
{
  "perf": {
    "budgets": { "api.hot.p95": 500, "api.interactive.p95": 1000, ... },
    "tiers": {
      "hot":       { "p50_ms": ..., "p95_ms": ..., "max_ms": ..., "n": ..., "over_budget_count": ... },
      "interactive": { ... },
      "batch":     { ... }
    },
    "control_cycle":  { "p95_ms": ..., "max_ms": ..., "n": ..., "overrun_count": ..., "last_overrun_at": ... },
    "rss_mb":         { "current": ..., "peak": ..., "over_ceiling_count": ... },
    "last_overruns":  [
      {
        "ts": "...",
        "name": "api.hot" | "control.cycle" | "store.history.write" | ...,
        "duration_ms": ...,
        "status": <http status, API only, null otherwise>,
        "path_template": "/api/battery-plan" | null,
        "phase": "decide" | null
      },
      ...
    ]
  }
}
```

Same shape as today's `recorder_health` block — additive, no breaking
changes to existing diagnostics consumers.

## 5. Tests

All in `ems/tests/test_perf_*.py`. Fail-first.

### 5.1 `test_sustained_dashboard_poll`

- Spin up `TestClient(app)` with canned fixtures (mock sources, canned
  history, canned prices).
- Fire all 11 H-tier routes × 20 rounds concurrently using
  `concurrent.futures.ThreadPoolExecutor` + `TestClient` (sync) or
  `httpx.AsyncClient` against the running app.
- Assert per route: `p95 < 500 ms` AND no round's slowest request grows
  > 50% vs round 1 (catches memory leaks / cumulative slowness).
- Captures samples via the same `Registry`; verifies both the data and
  the assertion.

### 5.2 `test_over_budget_control_cycle_forces_auto`

- Construct a `MockBatteryDriver` whose `apply()` records every call
  in a list. Configure it so that `apply(mode=<any non-AUTO>)` blocks
  for 25 s (`asyncio.sleep`); `apply(mode=AUTO)` returns immediately.
- Force `lifecycle.start(now)` so the wrapper does not treat this as
  grace-period.
- Run one control cycle through `run_cycle()`. The tick will reach
  `decide()`, then hang in `apply()` (simulating a stuck write).
- The `asyncio.wait_for(tick, timeout=20)` cancels the tick at 20 s.
- Assert:
  - `apply(mode=AUTO)` was called once (the safety write).
  - The intended write mode was called exactly once (the call that
    hung, before cancellation).
  - No further `apply(...)` calls happened.
  - Audit log contains `control.overrun` with `reason: "timeout"` and
    `duration_ms ≈ 20000` (allow 100 ms slack).
  - The registry has a `control.cycle` sample with
    `over_budget=True`.
- The 25 s blocking call is well past the 20 s budget, so this is a
  positive test of the safety behavior. The driver mock makes the
  test deterministic.

### 5.3 `test_over_budget_api_logs_warn`

- Make one H-tier route take 600 ms (mocked slow work in a fixture).
- Assert the registry has the sample with `over_budget=True`.
- Assert `/api/diagnostics` exposes it under `perf.tiers.hot`.

### 5.4 `test_rss_ceiling_sampled`

- Run the RSS sampler in a fast-forward mode (inject `interval_seconds=0.1`
  via a constructor arg; default 60 s in production).
- Assert it produces samples every interval and exposes `current`,
  `peak`, and `over_ceiling_count` via `/api/diagnostics.perf.rss_mb`.

### 5.5 `test_perf_budgets_match_spec`

- A guard test: assert `PERF_BUDGETS` in `ems/perf.py` matches the
  values in `docs/perf-budgets.md` (parse the table). Catches drift
  between code and docs.

### 5.6 `test_perf_middleware_is_pure_asgi`

- Assert `ems.web.perf_middleware.PerfTimingMiddleware` is a pure ASGI
  class (subclass of `object` with `__call__(self, scope, receive, send)`,
  not a `BaseHTTPMiddleware`). Mirrors the auth slice invariant and
  protects the override control cycle.

## 6. Local command — `make perf-check`

- Runs `python -m ems.tools.perf_check` (new tiny entrypoint).
- Boots a `TestClient(app)` with canned fixtures (same harness as the
  tests), exercises the same workload: one round of all 11 H-tier routes
  + a synthetic control cycle (with a fast mock) + one replay + one
  report build.
- Prints a Markdown table:
  ```
  | name                | tier | p50 (ms) | p95 (ms) | max (ms) | n | budget | pass |
  |---------------------|------|----------|----------|----------|---|--------|------|
  | api.hot             | H    | 42       | 187      | 312      | 11 | 500    | ✓   |
  | api.interactive     | I    | 18       | 64       | 98       |  6 | 1000   | ✓   |
  | api.batch           | B    | 1240     | 2410     | 2510     |  4 | 8000   | ✓   |
  | control.cycle       | -    | 820      | 820      | 820      |  1 | 20000  | ✓   |
  | store.history.read  | -    | 12       | 38       | 91       | 47 | 100    | ✓   |
  | store.history.write | -    | 41       | 142      | 233      | 12 | 500    | ✓   |
  | replay.run          | -    | 8120     | 8120     | 8120     |  1 | 30000  | ✓   |
  | report.build        | -    | 3120     | 3120     | 3120     |  1 | 30000  | ✓   |
  | memory.rss.peak     | -    | -        | -        | 184      |  1 | 350MB  | ✓   |
  ```
- Exits 0 if all green, 1 otherwise. Output is human-readable; not
  consumed by CI (per the earlier decision — local command only).

Lives at `ems/tools/perf_check.py` and is documented in
`docs/perf-budgets.md`. No changes to `Makefile` other than the new
`perf-check` target.

## 7. Implementation slices (for the plan)

1. **`ems/perf.py` + `PERF_BUDGETS` + `docs/perf-budgets.md` + the
   guard test (`5.5`).** No behavior change; lays the foundation.
2. **API perf middleware + `5.3`, `5.6` tests.** Pure ASGI; surface in
   `/api/diagnostics.perf.tiers`.
3. **Storage wrappers + budget tests.** `history.py`, `settings.py`,
   `audit.py`, `cache.py`, `control_state.py`.
4. **Control-cycle wrapper + `5.2` test.** The force-AUTO safety
   behavior; the most safety-critical slice.
5. **Replay / reporting wrappers + `5.1` sustained-dashboard-poll test
   + `5.4` RSS test.** Batch workloads.
6. **`make perf-check` + entrypoint + Markdown report.** Wires it all
   together.

Each slice is independently shippable; slice 4 is the safety-critical one
and ships with the test that proves the AUTO-on-overrun behavior works.

## 8. Out of scope (explicitly)

- Prometheus / OpenTelemetry / external monitoring.
- Rate-limiting incoming requests.
- Request cancellation on over-budget.
- CI gate (per the earlier decision — `make perf-check` is manual).
- Pi-specific budget numbers (those stay aspirational in SPEC §11;
  this design is Mac-truth).
- Changing the control tick logic itself.
- Changing the existing query-count perf tests in
  `ems/tests/test_reporting_perf.py` (they remain valid as query-count
  checks; this design adds *wall-clock* checks).

## 9. Open risks

- **Wall-clock tests on shared CI runners.** Not relevant here — the
  gate is local only — but if we ever add a CI gate later, the
  existing `time.sleep` tests would need addressing (out of scope per
  B-54).
- **Mac vs Pi divergence.** Today's production is Mac; Pi budgets are
  not enforced. If/when a Pi deploy happens, the perf-budgets doc must
  be re-measured against the Pi hardware. The `PERF_BUDGETS` constant
  is the lever to retune; the design itself doesn't change.
- **`CacheStore` per-call connection cost.** Wrapping it surfaces a
  known per-call cost (1–3 ms) on every strategy resolution. A
  future refactor could fix the root cause (long-lived connection),
  but that's out of scope here — measurement first, optimization
  later.