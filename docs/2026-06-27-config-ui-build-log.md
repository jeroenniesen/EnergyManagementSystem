# Config-in-the-UI build log (10-loop series)

Goal: surface the missing configuration options in the UI; for each part, run the code reviewer
and keep the test base strong; then find the next gap. 10 loops, then a build/gap report.

Baseline before this series: backend 103 → (loop 0 handoff fixes) 103, e2e 23 → 24.

| Loop | Feature | Backend | Frontend | Tests | Reviewer |
|------|---------|---------|----------|-------|----------|
| 1 | Runtime settings store + schema-driven `/api/settings` + Settings UI tab | `ems/settings.py`, `ems/storage/settings.py`, `/api/settings` GET/POST, live PlannerConfig + ModeController wiring | `Settings.tsx` (grouped form), Dashboard/Settings tabs | 124 pytest | backend: atomic cache swap, missing-body→422, dwell floor 60s, switch ceiling 20; frontend: saving-disable, number-input blur-commit, aria-current=page |
| 2 | Manual operator override (force one intent, time-boxed, auto-expire) | `ems/control/override.py`, generalised KV store (`table=`), `/api/override` GET/POST, override-aware `_effective_intent` in decision+alerts | `Override.tsx` dashboard card (apply/clear + duration) | 136 pytest, 30 e2e | confirmed fail-safe + single-writer; fixed `assert`→guard (`-O`-safe) in seconds_remaining |
| 3 | Theme applied (auto/light/dark) — completes the appearance setting | (none) | `theme.ts` (applyTheme + OS-auto), App wiring + `onSaved`, light-theme CSS vars | 136 pytest, 32 e2e | flash-on-load fixed (localStorage seed before mount) |
| 4 | PV-array settings (kWp/tilt/azimuth) wired live into the solar forecast | `orientation_factor`, mutable forecast attrs, `_apply_site_settings`, 3 site.* fields | "Solar array" settings group | 140 pytest, 32 e2e | duck-typing → explicit `_ems_site_configurable` opt-in marker |
| 5 | Battery/reserve settings + advisory charge-need readout | `charge_need.py`, 4 battery.* fields, `/api/charge-need` | ChargeTarget card (SoC bar + target + reason) | 147 pytest, 33 e2e | clamp target marker visible at 100% |
| 6 | Bearer-token auth on the two mutating endpoints; reads stay open | `_authorized`/`_auth_error`, `/api/auth`, gated POSTs, `EMS_WEB_TOKEN` env | `auth.ts`, Settings Access section + token on writes, Override 401 msg | 154 pytest, 35 e2e | non-ASCII token → clean 401 (wrap compare_digest) |
| 7 | "System" diagnostics page — readiness checks + overall status | `diagnostics.py`, `/api/diagnostics` (probes stores/battery/plan) | `System.tsx` nav tab + checks list | 164 pytest, 36 e2e | battery.probe via to_thread+guard (no loop block/500); Check validates status; poll gated to dashboard view |
| 8 | CSV/JSON history export | public column consts, `/api/export?kind&format&limit` | System view download links | 169 pytest, 38 e2e | Content-Disposition filename hardened (no header injection) |
| 9 | Data-quality fail-safe gate — unsafe data falls back to self-consumption (AUTO) | `control/failsafe.py`, `_data_quality` single source, gated `_effective_intent` | (reason surfaces in decision panel) | 175 pytest, 38 e2e | alerts badge+list from one snapshot (no race) |

Final state on `main`: **175 pytest, ruff clean, 38 Playwright, bundle ~51 kB gz.** 16 UI-editable
settings (was 0); 20 HTTP endpoints; every loop reviewed by the code-reviewer subagent and its
high-confidence findings fixed before merge.

## What is still needed (remaining SPEC gaps)

These are mostly **M1+ items gated on real hardware/integrations** — they can't be exercised in
this mock / dry-run environment, so they were out of scope for a config-in-the-UI series:

**Live integration & control (M1):** live HA client + HomeWizard/Tibber/Solcast adapters (replace
the `Mock*` sources); M1a real Indevolt capability probe + M1b confirmed idempotent battery writes;
the live control loop actually calling `controller.decide()` on a schedule; persisting the
controller's switch counters / lifecycle across restarts (the `runtime_state` table exists — used
by the override — but the counters are still in-memory).

**Planner depth (M2/M3):** the full target-SoC planner with a projected-SoC curve and the complete
§8.11 validator (PlannerInputSnapshot, plan versioning); summer solar (charge-to-target-by-sunset
via astral, needs lat/lon); economics (grid fees, export tariff, midday negative-price policy,
`hold_reserve.allow_solar_charge`, daily-min-savings, cycle budget, P10/P50 sizing). Today: price
arbitrage + advisory charge-need + the unsafe→AUTO gate (a safety subset of §8.11).

**More UI config:** location (lat/lon/timezone) + the Leaflet setup map / `/setup` wizard;
planner-mode switch (rule_based|ml|advisory); grid-fees/export-tariff knobs; dashboard poll interval.

**Real-time & integration surfaces:** WebSocket/SSE live push (today: 5 s polling); MQTT publish of
decisions; replay mode.

**ML layer (M6, accelerator-gated):** LoadForecaster / MlPlanner / explainer
(template|local_llm|external_llm e.g. MiniMax) behind `ports.py` + `ems/ml/`. None built.

**Ops:** Docker Compose (Pi + Jetson), CI, live-secret wiring. **EV control = separate v2 spec.**
