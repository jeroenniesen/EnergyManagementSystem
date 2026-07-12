# EV charge control — v2 specification (placeholder)

> **Status: control not started — still a placeholder.** This is a deliberate stub so references
> from `../SPEC.md` (§2, §6.4, §16, §18) resolve. EV charge *control* remains **out of scope until
> this document is written**. What **has** shipped is a v1 **advisory/visual** layer (below) — the
> Tesla is still a **read-only, planned-around load**, measured by the HomeWizard car meter
> (`../SPEC.md §6.4`); nothing in v1 commands a charger or the car.

## v1 advisory layer (exists today)

Design: [`../docs/superpowers/specs/2026-07-12-ev-charging-design.md`](superpowers/specs/2026-07-12-ev-charging-design.md); spec summary: `../SPEC.md` §16 "EV charging advice (v1: visual only)". Modules:

- `ems/cars.py` — static database of popular EU EVs (usable capacity + onboard AC limit) for the brand/model picker.
- `ems/ev_schedule.py` — the weekly per-day-of-week minimum-SoC schedule (`enabled`/`min_pct`/`ready_by`) and DST-safe deadline materialization.
- `ems/ev_session.py` — charging-session detection from the HomeWizard car meter + the manual-anchor SoC estimate (`soc = anchor + measured kWh × η_c / capacity`; driving not modeled).
- `ems/ev_planner.py` — the pure "math core": deadline-driven, cheapest-slot-first charge planning (solar surplus valued at feed-in), with a brute-force cost-optimality cross-check in its tests.
- `ems/web/api.py` — `GET /api/cars` (picker data), `GET /api/car/plan` (plan + advice), `POST /api/car/soc` (the manual anchor; auth-gated + audited like `POST /api/control/override`).
- Web: a Settings "Car" group (picker + schedule editor) and a dashboard Car card (SoC, next deadline, plug-in windows). iOS: a `CarPanel`. Export: `ev_sessions.csv` + a car plan/schedule snapshot.

All of the above is **advisory only** — it recommends a plug-in window, never actuates a charger or the car, and shares no code with the battery writer (`ems/sources/battery.py`) or `ems/control/`; the existing car-guard (never discharge the home battery into the car, `../SPEC.md §4.5`) is untouched.

### The v2 seam this leaves

The planner's `slots` output (`start`, `kw`, `ac_kwh`, `for_deadline`, …) **is** the future control schedule: a charger driver would consume it behind the same intent→confirm pattern as the battery (single writer, dry-run first) once this spec exists (design doc, "v2 seam"). Everything below still needs to be worked out first — this stub is not yet that spec.

## Why this is its own spec

Controlling charging carries its own auth, safety, rate-limit, and UX complexity that would bloat the core HEMS spec. It is intentionally **not** a milestone of the main build plan (`../SPEC.md §15`).

## Scope to specify here (when written)

- **Access path** (pick one; see `../docs/api-reference.md` "Tesla Model Y"):
  - **Tesla BLE** (ESP32 + `yoziru/esphome-tesla-ble`) — local, no fees, best for solar-surplus tracking; car must be in BLE range.
  - **Tessie** (~$13/mo, HA core integration, handles signing) — zero hardware, remote reach.
  - **Teslemetry** / **Tesla Fleet API** (official, command signing + self-hosted HTTP proxy).
- **Commands & bounds:** `charge_start` / `charge_stop` / `set_charging_amps` / `set_charge_limit`; amp/limit min–max are **undocumented — read at runtime**, never hardcode.
- **Safety / hygiene (mirror the battery contract):** coarse, **debounced** start/stop + amp steps with a **minimum dwell time**; never a fast loop — frequent amp changes wake the car, burn cloud credits, and hit rate limits (~30 cmd/min).
- **Integration with the planner:** EV charging becomes a *controllable* load/intent (soak cheap/solar windows) rather than only a planned-around load; define how it interacts with the `BatteryIntent` plan, the §8.11 validator, and the dry-run gate.
- **Fail-safe:** on any uncertainty, **stop commanding the car** and fall back to letting it charge on its own schedule — never worse than "no EMS".
- **UX:** its own controls, explainability ("charging the car now because…"), and failure modes.

*Until this is written, do not implement EV control.*
