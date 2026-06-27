# Config-in-the-UI build log (10-loop series)

Goal: surface the missing configuration options in the UI; for each part, run the code reviewer
and keep the test base strong; then find the next gap. 10 loops, then a build/gap report.

Baseline before this series: backend 103 → (loop 0 handoff fixes) 103, e2e 23 → 24.

| Loop | Feature | Backend | Frontend | Tests | Reviewer |
|------|---------|---------|----------|-------|----------|
| 1 | Runtime settings store + schema-driven `/api/settings` + Settings UI tab | `ems/settings.py`, `ems/storage/settings.py`, `/api/settings` GET/POST, live PlannerConfig + ModeController wiring | `Settings.tsx` (grouped form), Dashboard/Settings tabs | 124 pytest | backend: atomic cache swap, missing-body→422, dwell floor 60s, switch ceiling 20; frontend: saving-disable, number-input blur-commit, aria-current=page |
| 2 | Manual operator override (force one intent, time-boxed, auto-expire) | `ems/control/override.py`, generalised KV store (`table=`), `/api/override` GET/POST, override-aware `_effective_intent` in decision+alerts | `Override.tsx` dashboard card (apply/clear + duration) | 136 pytest, 30 e2e | confirmed fail-safe + single-writer; fixed `assert`→guard (`-O`-safe) in seconds_remaining |
| 3 | Theme applied (auto/light/dark) — completes the appearance setting | (none) | `theme.ts` (applyTheme + OS-auto), App wiring + `onSaved`, light-theme CSS vars | 136 pytest, 32 e2e | flash-on-load fixed (localStorage seed before mount) |
| 4 | PV-array settings (kWp/tilt/azimuth) wired live into the solar forecast | `orientation_factor`, mutable forecast attrs, `_apply_site_settings`, 3 site.* fields | "Solar array" settings group | 140 pytest, 32 e2e | (running) |

## Planned remaining loops (revised — astral not installed, so dependency-free, higher-value config first)
- L5: Battery & reserve settings (usable kWh, min reserve SoC, night reserve, overnight load) + a daily charge-need readout (target SoC, deficit, on-track) — config + a real explainable computation.
- L6: Auth (bearer token) on mutating endpoints; read-only guest.
- L7: Setup/diagnostics page (`/api/diagnostics`) + UI.
- L8: Data export (`/api/export` CSV/JSON) + UI.
- L9: Plan validator / accessibility & polish sweep.
- L10: Assess remaining SPEC gaps; write the build/gap report.
