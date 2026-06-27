# Energy Management System (HEMS)

A **mode-switching home energy manager** that smart-charges an Indevolt home battery using a free solar forecast and dynamic (Tibber) electricity prices.

- **Summer:** fill the battery from solar surplus so the house runs the whole night on battery (+ a reserve).
- **Winter:** charge at the daily price *dip* and discharge during the price *peaks* (arbitrage).
- **Built-in web UI** with graphs (price, forecast vs actual, SoC, mode timeline, savings) and a **map** to set your location.
- Runs on a **Raspberry Pi** next to Home Assistant.

## 📖 Start here

**[`SPEC.md`](./SPEC.md)** is the single source of truth — the full plan *and* application spec (architecture, APIs, decision logic, config, deployment, build plan). Read it before building.

Supporting docs live in **[`docs/`](./docs/)**:
- [`docs/api-reference.md`](./docs/api-reference.md) — concrete endpoint/auth cheat-sheet (incl. the exact Tibber quarter-hour query).
- [`docs/energy-model.md`](./docs/energy-model.md) — sign conventions, house-load reconstruction, data dictionary, calibration.
- [`docs/control-model.md`](./docs/control-model.md) — control plane: P1-zeroing contract, `BatteryIntent`, target-SoC math, the `Plan` object + validator, ownership state machine, missed-window recovery.
- [`docs/config-reference.md`](./docs/config-reference.md) — full per-key config reference.
- [`docs/ml-layer.md`](./docs/ml-layer.md) — the optional ML layer: ports, the `rule_based`/`ml`/`advisory` switch, training, local-LLM explainer, fallback.
- [`docs/jetson-deployment.md`](./docs/jetson-deployment.md) — Jetson deployment (EMS + ML on the Jetson, HA on the LAN).
- [`docs/failure-modes.md`](./docs/failure-modes.md) — detection → safe-behaviour → recovery for every failure.
- [`docs/operator-runbook.md`](./docs/operator-runbook.md) — disable EMS, force AUTO, rotate tokens, restore backups.

The north-star vision is in **[`GOAL.md`](./GOAL.md)**; agent/project conventions in **[`CLAUDE.md`](./CLAUDE.md)**.

## System at a glance

| Component | Choice |
|---|---|
| Battery | Indevolt **SolidFlex 2000 (Gen-2), 2-tower cluster** — driven via the official HA `indevolt.*` services (RPC fallback), **mode-switching only** |
| Prices | **Tibber** GraphQL API (15-min `priceInfoRange`), EnergyZero free fallback |
| Solar forecast | **Solcast** Hobbyist primary (P10/P50/P90), **Forecast.Solar** keyless fallback |
| Live power | **HomeWizard** P1 + 2× kWh meters (solar, car) — local |
| EV | Tesla Model Y — read-only in v1 (HomeWizard car meter); charge control is a v2 add-on |
| Hub | **Home Assistant** for integrations + history |
| EMS Core | Standalone **Python / FastAPI** service: forecaster · planner (port) · mode controller · **SQLite history** |
| UI | **React + Vite** SPA, served by the EMS, **no runtime CDN**; bundled charts + Leaflet |
| Optional ML | **Accelerator-gated** (behind `ports.py`): learned load forecast · learned planner · LLM explainer; runtime switch `rule_based`/`ml`/`advisory`; never bypasses the plan validator. Explainer also offers an **`external_llm`** (cloud API, e.g. MiniMax) that works on a plain Pi |
| Hardware | **Raspberry Pi 5** (CPU-only core) **· Nvidia Jetson** (CUDA ML; HA on the LAN) **· Apple Silicon** (Metal/MLX — dev/test); Docker Compose |

## Build order

`M0a` ingest + store + scaffolding → `M0b` React+Vite dashboard + setup → `M0c` prices/forecasts normalised → `M1a` battery **read-only capability probe** → `M1b` battery writes → `M2` winter arbitrage (dry-run → enable) → `M3` summer solar (dry-run → enable) → `M4` polish (savings, guardrails, auth, alerts, visual polish) → `M6` **optional ML layer** (Jetson-gated; advisory → ml). **EV control is a separate v2 spec**, not a milestone here. See `SPEC.md` §15.

## Status

**Implementation-ready draft** — design complete and reviewed; several device-specific values still need **M0/M1 hardware validation** (see the validation checklist + *Known uncertainties* table in `SPEC.md`). Implementation not yet started. The `ems/` service tree is in `SPEC.md` §13.
