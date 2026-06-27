# Config-in-the-UI build log (10-loop series)

Goal: surface the missing configuration options in the UI; for each part, run the code reviewer
and keep the test base strong; then find the next gap. 10 loops, then a build/gap report.

Baseline before this series: backend 103 → (loop 0 handoff fixes) 103, e2e 23 → 24.

| Loop | Feature | Backend | Frontend | Tests | Reviewer |
|------|---------|---------|----------|-------|----------|
| 1 | Runtime settings store + schema-driven `/api/settings` + Settings UI tab | `ems/settings.py`, `ems/storage/settings.py`, `/api/settings` GET/POST, live PlannerConfig + ModeController wiring | `Settings.tsx` (grouped form), Dashboard/Settings tabs | 124 pytest | backend: atomic cache swap, missing-body→422, dwell floor 60s, switch ceiling 20; frontend: saving-disable, number-input blur-commit, aria-current=page |
| 2 | Manual operator override (force one intent, time-boxed, auto-expire) | `ems/control/override.py`, generalised KV store (`table=`), `/api/override` GET/POST, override-aware `_effective_intent` in decision+alerts | `Override.tsx` dashboard card (apply/clear + duration) | 136 pytest, 30 e2e | (running) |

## Planned remaining loops (subject to revision as gaps are found)
- L3: Theme (auto/light/dark) applied to the DOM — completes the appearance setting.
- L4: Site/location settings (lat/lon/tilt/azimuth/kWp/timezone) wired into the solar forecast.
- L5: Battery & reserve settings + a real daily-charge-need planner (target SoC, projected curve).
- L6: Auth (bearer token) on mutating endpoints; read-only guest.
- L7: Setup/diagnostics page (`/api/diagnostics`) + UI.
- L8: Data export (`/api/export` CSV/JSON) + UI.
- L9: Plan validator (projected-SoC) / accessibility & polish sweep.
- L10: Assess remaining SPEC gaps; write the build/gap report.
