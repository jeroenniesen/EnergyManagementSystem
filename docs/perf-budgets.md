# Performance budgets

> Single source of truth for response-time and resource budgets in EMS.
> Mirrored in code as `ems.perf.PERF_BUDGETS`. The guard test
> `ems/tests/test_perf.py::test_perf_budgets_match_spec` fails on drift.

| Name | Tier | Budget | Where it applies |
|---|---|---|---|
| api.hot | H | 500 ms | 11 dashboard-10s routes + override poll |
| api.interactive | I | 1000 ms | on-mount routes (`/api/settings`, `/api/cars`, `/api/forecast`, `/api/auth/me`, etc.) |
| api.batch | B | 8000 ms | `/api/export/package`, `/api/report?period=year`, `/api/counterfactual`, `/api/digest` |
| control.cycle | - | 20 s | one `_run_control_cycle()` |
| store.history.read | - | 100 ms | history-store read transactions |
| store.history.write | - | 500 ms | history-store write transactions |
| store.settings.read | - | 50 ms | settings-store read transactions |
| store.settings.write | - | 200 ms | settings-store write transactions |
| store.audit.append | - | 200 ms | audit-store append |
| store.cache.get | - | 5 ms | cache-store get (sync per-call connection) |
| store.cache.set | - | 5 ms | cache-store set (sync per-call connection) |
| store.control_state.read | - | 50 ms | control-state-store read |
| store.control_state.write | - | 200 ms | control-state-store write |
| replay.run | - | 30 s | `ems.replay.run_replay(...)` |
| report.build | - | 30 s | `ems.reporting.build_report(...)` year/week assembly |
| memory.rss.peak | - | 350 MB | process RSS ceiling, sampled every 60 s |

## Over-budget behavior

See design spec §3. The headlines:

- **API:** log WARN; request completes normally; sample pushed to registry. No cancellation.
- **Control cycle:** audit `control.overrun`; if `not dry_run` and past lifecycle grace, force `driver.apply(mode=AUTO)` before returning. In dry-run or grace, log only.
- **Stores / replay / report:** log WARN only.
- **RSS over 350 MB:** log WARN once per minute; expose in diagnostics.

## Local check

Run `make perf-check` from the repo root. Prints a Markdown table of measured
percentiles vs the budgets above. Exits 0 if all green, 1 otherwise.
