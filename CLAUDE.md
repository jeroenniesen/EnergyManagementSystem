# CLAUDE.md — Energy Management System (HEMS)

Project context and conventions for agents working in this repository.

## What this project is

A home energy management system that **switches the operating mode of an Indevolt home battery** based on a solar forecast and dynamic Tibber prices. Goal: run the house on battery overnight in summer; arbitrage cheap/expensive price windows in winter.

**Current (production today):** **Mac Mini (Apple Silicon)** · `scripts/install.sh` → `uv` venv + **launchd LaunchAgent** (`com.jeroenniesen.ems`) · **direct-device** reads/writes · **no required Home Assistant or MQTT** for live control.

**Target (documented, not running):** Raspberry Pi (+ HA hub) per SPEC §11.1; Jetson option for the ML sidecar (`docs/jetson-deployment.md`). HA client wiring / `entity_map` / MQTT discovery = **planned, not implemented** — [B-18 / #107](https://github.com/jeroenniesen/EnergyManagementSystem/issues/107).

**`SPEC.md` is the source of truth.** Read it before implementing anything. `docs/api-reference.md` is the endpoint/auth cheat-sheet. If code and SPEC disagree, the SPEC wins — update it deliberately, don't drift from it. (Reconcile: B-26 / [PR #25](https://github.com/jeroenniesen/EnergyManagementSystem/pull/25); refresh [#182](https://github.com/jeroenniesen/EnergyManagementSystem/issues/182).)

## Non-negotiable design constraints

- **Mode-switching, NOT continuous control.** The Indevolt OpenData API recommends ≥5 s between writes. Only command the battery **when the desired mode changes** (target < 10 writes/day). Never build a tight power-tracking loop.
- **Indevolt owns P1 zeroing — don't fight vendor control.** In self-consumption the battery runs its own fast controller against the P1 meter; the EMS sets *intent/mode* and never nudges live power to correct minor deviations. Whether P1 zeroing stays active per mode is **verified and stored at M1** (`CapabilityReport`), not assumed. See SPEC §2/§6.5 and `docs/control-model.md`.
- **Plan in `BatteryIntent` + target SoC + deadlines, not raw commands.** The planner emits a high-level intent (`ALLOW_SELF_CONSUMPTION` / `GRID_CHARGE_TO_TARGET` / `HOLD_RESERVE` / `DISCHARGE_FOR_LOAD`) with a **target SoC derived from required kWh** and a **deadline** (summer=sunset, winter=first peak); `mode_controller` maps intent→physical mode→probed vendor action. See SPEC §7–§8.
- **Validate the plan; observe before acting.** Every `Plan` is versioned, carries a `PlannerInputSnapshot`, and passes a **plan validator** + data-quality badge before it can be applied (`unsafe` ⇒ stay `AUTO`). On boot, **observe → validate sensors → load plan → only then act**, after a startup grace period. See SPEC §8.11/§13.
- **The planner is a port; ML never bypasses safety.** Both the rule-based planner and the optional ML planner emit the **same** `Plan` and pass the **unchanged** §8.11 validator + guardrails. The runtime **planner-mode switch** is `rule_based` (default) | `ml` | `advisory` (`planner.mode` in the settings store; `ml`/`advisory` fall back to `rule_based` until M6). The ML forecaster/optimizer is **optional and accelerator-gated** (CUDA on Jetson, Metal/CoreML/MLX on Apple Silicon; off on a plain Pi); when a model/accelerator is absent, slow, or low-confidence, **fall back** to the baseline/rule-based path and alert. It lives behind `ports.py` in `ems/ml/` — keep accelerator deps out of the Pi image. The **`explainer` is separate and NOT accelerator-gated**: `template` (default) / `local_llm` (accelerator) / `external_llm` (cloud API, e.g. MiniMax — works on a Pi; off by default, minimal redacted payload, privacy §12). See `docs/ml-layer.md`.
- **One battery writer.** All battery writes go through `ems/sources/battery.py`. Nothing else writes to the battery — including any future HA write client.
- **Probe the Indevolt surface before assuming it.** Per the official HA integration, **only `indevolt.charge` and `indevolt.discharge` are services**; "standby/idle" is a **button** entity and "energy mode" is a **select** entity (there is **no** `indevolt.stop`/`indevolt.change_mode`). Today's shipped path is **direct OpenData RPC** (`ems/sources/indevolt.py` / `indevolt_driver.py`); the HA capability-probe-via-services flow is **planned** (B-18/#107). The cluster is controlled as **one logical device**. See SPEC §6.5.
- **P1 is *net grid flow*, not house load.** House load is **reconstructed**: `house_load = grid + solar + battery_power` (see SPEC §4 / `docs/energy-model.md`). Never read any single meter as "house consumption". Sign conventions are fixed and normalised in `load_model.py`.
- **Fail safe.** If prices/forecast/meters are stale or anything is uncertain, fall back to the battery's own `AUTO` (self-consumption) mode. The system must never be worse than "no EMS".
- **Dry-run before every live strategy.** New control logic ships behind dry-run (log decisions, no writes) for a multi-day acceptance period; only enable writes after comparing plan vs. actual. Honour `max_mode_switches_per_day` **and** a minimum mode dwell time. **Two floors:** (1) `config.yaml` `control.dry_run` (or `dev.mode: mock`/`replay`) always forces watch-only over the UI (#136); (2) Settings **Watch only** + **`control.operational`** in the runtime settings store (#171). Live writes need `dev.mode: live` + yaml `dry_run: false` + Watch only OFF + operational ON.
- **Direct-device reads/writes today; HA path planned (B-18/#107), not live.** The shipped control path reads HomeWizard/Tibber/Solcast|Forecast.Solar and reads+writes the Indevolt battery **directly**. `ems/sources/ha.py` is a **read-only skeleton** (tested) — **not wired** into `sense` / `connection.py` / config / startup validation. There is **no** MQTT publisher (`paho-mqtt` not a dependency), **no** `entity_map` in shipped `config.yaml`. The HA-mediated architecture remains the SPEC §5/§9.2 **target** for the Pi deployment; when built, HA becomes required for live telemetry and the web UI degrades read-only during an HA outage.
- **Explainability first.** Every decision carries a human-readable reason — **including why it is *not* acting** — surfaced in the web UI and *(optionally, when B-18/#107 ships)* MQTT.
- **Metrics = diagnostics only.** Operator surfaces (`/api/diagnostics`, freshness, incidents) — **no Prometheus** exporter.

## Architecture

**Target** two layers (SPEC §5):
1. **Home Assistant** = integration hub + system of record *(planned — B-18/#107)*.
2. **EMS Core** = standalone **Python 3.12 / FastAPI** service holding all decision logic, serving its **own web UI with graphs**, and owning a local **SQLite** history store.

**Current:** EMS Core alone on the Mac Mini, talking to devices directly; HA/MQTT optional layers are absent from the live path.

Control loop every `cycle_seconds` (default 300 s): **sense → reconstruct load → (re)plan if stale → decide → act only on mode change → confirm → record → publish**.

## Hardware (confirmed)

- Battery: **Indevolt SolidFlex 2000, Gen-2, 2 towers clustered (~10.8 kWh), latest firmware.** Gen-2 power/feed-in/grid-charge controls available. Exact max charge/discharge watts (~4 kW) to be read from the device.
- Solar: **3 kWp**. Tibber dynamic contract. Tesla Model Y. HomeWizard meters: **P1 (grid) + kWh (solar) + kWh (car)**. Existing Home Assistant on the LAN is **not** required by today's EMS. Deploy: **Mac Mini (current)**; **Raspberry Pi 5** (CPU-only core, target) **or Nvidia Jetson** (optional GPU ML layer; HA then runs elsewhere on the LAN — see `docs/jetson-deployment.md`).

## Tech stack

- **Backend:** Python 3.12, `asyncio`, **FastAPI** + Uvicorn, `httpx`, `aiosqlite`, `pyyaml`, `ems/sky.py` (sunrise/sunset — not `astral`), optional `timezonefinder` (offline tz). MQTT (`paho-mqtt`) is **target-only**, not a current dependency.
- **Frontend: React + Vite** SPA, **built at image-build time** and served by FastAPI (SPA fallback). **No runtime CDN** — all deps bundled/self-hosted (charts, **Leaflet** + its assets, fonts, icons); OSM tiles are the one online resource, only on `/setup`. Bundle ≤300 KB gz, WCAG 2.1 AA, light/dark, English-only v1. Playwright + visual-regression for UI tests.
- **Optional ML layer** (`ems/ml/`, **accelerator-gated** — CUDA on Jetson, Metal/CoreML/MLX on Apple Silicon, off on a plain Pi): `LoadForecaster` / `MlPlanner` / `LocalLlmExplainer` behind `ports.py`, selected by the **planner-mode switch** (`rule_based`|`ml`|`advisory`). The **`explainer`** is separate (not accelerator-gated): `template` / `local_llm` / `external_llm` (cloud API). Never bypasses the §8.11 validator. See `docs/ml-layer.md`. **`ems/ml/` is not packaged yet** (M6).
- **Deploy — current:** Mac Mini LaunchAgent via `scripts/install.sh` (no Docker required). **Target:** Docker Compose — **Pi:** HA + Mosquitto + EMS on one host (`SPEC §11`); **Jetson:** lean EMS image + GPU ML sidecar; HA/Mosquitto remote over LAN (`docs/jetson-deployment.md`). EMS web UI on port **8080**; SQLite + models in `/data`.

## Repository layout

**Target sketch** (SPEC §13) includes HA/MQTT/ML modules that are not all live. **Shipped reality highlights:**

```
SPEC.md            # source of truth
docs/              # supporting documentation
ems/               # the service (see SPEC §13 for the full reconcile callout)
  main.py  config.py  settings.py  sky.py  sense.py  connection.py
  sources/{live,tibber,forecast,forecast_solar,solcast,battery,indevolt,indevolt_driver,ha}.py
                 # ha.py = read-only skeleton only (B-18/#107) — not wired
  planner/{rule_based,summer,adaptive,validator,factory,…}.py
  control/{mode_controller,failsafe,loop,override,safety,…}.py
  storage/{history,settings,control_state,audit,cache}.py
  web/{api.py, context.py, routes/*, static/}
  # no ems/publish/ yet — MQTT discovery planned (B-18/#107)
  # no ems/ml/ yet — M6
  tests/
scripts/install.sh # Mac LaunchAgent / Linux systemd --user
docker-compose.yml # target Pi/dev sketches
```

## Conventions

- **Config:** `config.yaml` holds read-only **defaults** (minimal starter); UI-editable values (location/pin, tilt/azimuth, night reserve, percentile, mode override, Watch only / operational, `planner.mode`) live in a **runtime settings store** in `/data` and overlay the file. Effective config = defaults + runtime settings.
- **Planner granularity:** 15-minute slots (NL day-ahead is quarter-hourly; Tibber `priceInfoRange` is under `currentSubscription`, hourly auto-expands to 4×15min). Use Solcast **P50 for the expected case, P10 for commitments** (grid-charge sizing, overnight guarantee) — risk-aware sizing.
- **Economics, not magic numbers:** arbitrage runs only when `net_benefit = avoided_price − charge_price/efficiency − degradation − risk_margin (± grid fees) > 0`; no-trade below `daily_min_savings_eur`. Respect a cycle budget.
- **Testing:** planners must be unit-testable with canned prices/forecasts and a mocked battery — no hardware in tests. Add tests with planner logic.
- **Dry-run:** new control logic ships behind dry-run (log decisions, no writes) before going live — honour both yaml and Settings floors above.
- **Secrets:** Tibber/Solcast tokens, HA token, web auth token via `!secret` / env — never commit secrets.

## Build order

`M0a` ingest + store + scaffolding (load reconstruction, SQLite, `ports.py`/domain objects) → `M0b` **React+Vite** dashboard + setup map (+ Playwright/visual harness) → `M0c` prices/forecasts normalised to 15-min slots → `M1a` battery **read-only capability probe** → `M1b` battery writes (idempotent + confirmed) → `M2` winter arbitrage (dry-run → enable) → `M3` summer solar (dry-run → enable) → `M4` polish (savings, guardrails, auth, alerts, visual-polish passes) → `M6` **optional ML layer (Jetson-gated)**: advisory first, then `ml`. **EV control = separate v2 spec**, not a milestone. Each milestone is independently useful; see SPEC §15. **HA client + MQTT (B-18/#107) is not a core milestone gate for Mac Mini production.**
