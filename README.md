# Energy Management System (HEMS)

A **mode-switching home energy manager** that smart-charges an Indevolt home battery using a free
solar forecast and dynamic (Tibber) electricity prices.

- **Summer:** fill the battery from solar surplus so the house runs the night on battery (+ reserve).
- **Winter:** charge at the daily price *dip* and discharge during the price *peaks* (arbitrage).
- **`auto`:** picks the strategy from the actual forecast surplus + price spread, not the calendar.
- **Built-in web UI** with graphs (price, forecast vs actual, SoC, mode timeline, savings), a map to
  set your location, plain-language explanations, and an optional AI chat.
- **Fail-safe by design:** ships in simulation, and **never writes to the battery** until you
  deliberately turn on operational mode — sensing is read-only.

## Install on a Mac (one command)

On a Mac (Apple Silicon — e.g. a Mac Mini M5), run this single command:

```bash
curl -fsSL https://raw.githubusercontent.com/jeroenniesen/EnergyManagementSystem/main/scripts/bootstrap.sh | bash
```

That's it. It downloads the app to `~/EnergyManagementSystem` and bootstraps everything (no sudo, no
Homebrew required): installs `uv` if you don't have it, fetches a local Node only if needed, builds
the dashboard, sets up the Python environment, installs a small auto-start service (it comes back
after a reboot), starts the app, and prints the URL. Then open **http://localhost:8080**.

<details><summary>Prefer to clone first?</summary>

```bash
git clone https://github.com/jeroenniesen/EnergyManagementSystem.git
cd EnergyManagementSystem
./scripts/install.sh        # or: make install
```
</details>

### Upgrading

To update a running install to the latest version (rebuilds + restarts; **your data and settings are
kept**), run this one command on the Mac:

```bash
curl -fsSL https://raw.githubusercontent.com/jeroenniesen/EnergyManagementSystem/main/scripts/upgrade.sh | bash
```

(From a checkout you can also just `make upgrade`.)

### Managing it

```bash
make restart      # restart after changing device/connection settings (or: ./scripts/restart.sh)
make uninstall    # stop + remove the auto-start service (keeps your data)
make dev          # run in this terminal instead of as a service (./scripts/install.sh --foreground)
```

Most settings (strategy, planner, AI, theme) apply instantly. **Device/connection changes** — meter
IPs, the Tibber token, the live-devices toggle — take effect on the next restart; the UI flags those
fields. Restart from anywhere on the Mac with: `launchctl kickstart -k gui/$(id -u)/com.jeroenniesen.ems`.
Logs are at `ems/data/server.log`.

## Everything is configured in the web UI

**This repository contains no credentials, IP addresses, or tokens** — it ships in credential-free
*simulation* mode. You set up your own system entirely in the UI (Settings), and your values are
stored locally in `ems/data/` (which is never committed):

| In the UI you set… | for… |
|---|---|
| **Connection** — "use live devices / live prices" | switch from the simulator to your real hardware |
| **Energy meters** — HomeWizard P1 / solar / car IPs | live power + house-load reconstruction |
| **Battery** — Indevolt IP(s), capacity, reserve | battery sensing (and, later, control) |
| **Electricity prices** — your Tibber token | live day-ahead prices |
| **Solar & location** — pin on a map, tilt/azimuth/kWp | the solar forecast |
| **AI explanations & chat** — MiniMax API key (optional) | natural-language explanations + chat |
| **Access & security** — an optional access token | require a token to change settings/control |

Nothing here needs an environment variable or a config file edit — it's all in the UI.

## Safety

- Ships in **mock + dry-run**: the simulator runs with no devices, and the battery is never written.
- Going live is two deliberate, separate steps in the UI: turn on **live devices** (read-only
  sensing), and only much later turn on **operational** control — which is gated behind layered
  readiness + a hard plan validator, and stays off by default.
- Reads always work for a guest; only *changes* can be protected by the access token.

## Developing

```bash
make test     # Python test suite (uv run pytest)
make lint     # ruff
make build    # build the React/Vite dashboard
make e2e      # Playwright end-to-end tests (hermetic: isolated DB, forced mock)
make dev      # run in the foreground on this machine
```

A `Dockerfile` is included for container deployments (Raspberry Pi / server).

## Architecture & docs

**[`SPEC.md`](./SPEC.md)** is the single source of truth (architecture, APIs, decision logic,
config, deployment). Supporting docs in **[`docs/`](./docs/)**:

- [`docs/control-model.md`](./docs/control-model.md) — P1-zeroing contract, `BatteryIntent`,
  target-SoC math, the `Plan` object + validator, ownership state machine.
- [`docs/energy-model.md`](./docs/energy-model.md) — sign conventions, house-load reconstruction.
- [`docs/config-reference.md`](./docs/config-reference.md) — every config key.
- [`docs/dashboard-navigation.md`](./docs/dashboard-navigation.md) — where things live in the web UI: Dashboard vs. contextual drawers vs. Manage vs. Insights, deep links, and back behaviour.
- [`docs/live-integration.md`](./docs/live-integration.md) — wiring real HomeWizard/Tibber/Indevolt.
- [`docs/operator-runbook.md`](./docs/operator-runbook.md) — disable EMS, force AUTO, restore.
- [`docs/ml-layer.md`](./docs/ml-layer.md) — the optional, accelerator-gated ML layer.

Project conventions are in [`CLAUDE.md`](./CLAUDE.md); the north-star vision in [`GOAL.md`](./GOAL.md).

## System at a glance

| Component | Choice |
|---|---|
| Battery | Indevolt **SolidFlex 2000 (Gen-2)** cluster — **mode-switching only**, one logical device |
| Prices | **Tibber** day-ahead (15-min) |
| Solar forecast | **Forecast.Solar** (keyless) with a built-in model fallback |
| Live power | **HomeWizard** P1 + kWh meters (solar, car) — local, read-only |
| EMS Core | **Python 3.12 / FastAPI** — planner · mode controller · §8.11 validator · **SQLite** history |
| UI | **React + Vite** SPA served by the EMS, no runtime CDN |
| Optional AI | `template` (default) / `external_llm` (MiniMax) explainer + chat — minimal redacted payload |
| Hardware | **Mac (Apple Silicon)** · **Raspberry Pi 5** · containerised |

## What you'll see

- **Dashboard** — live status, the current strategy, the next-24h plan, and today's energy Sankey.
- **Car card** (optional, off by default) — set a weekly minimum-charge schedule and it tells you the
  cheapest window to plug in before each ready-by, from a manual SoC anchor plus the car's measured
  charging (dashboard and iOS app). Advisory only — v1 never controls a charger or the car.
- **Insights** — three 0–100 scores that trend over **day / week / month / year**: *self-consumption*
  (solar kept on-site), *CO₂* (% avoided vs. a no-solar/battery/EMS home), and *best-price* (how well
  grid imports were timed) — each explaining itself — plus a *where-your-energy-went* panel (kWh from
  solar/grid/battery → house/car). Read-only, rolled up from local history.
- **Chat · Settings · Audit · System** — assistant, configuration, the decision log, and health checks.
  The System page also offers a one-click **export package**: your whole history as CSVs (energy,
  prices, solar forecast vs. actual, plan history, daily savings, gas & CO₂, decision log) plus a
  plain-language health summary — redacted (no tokens, IPs or location), for your own analysis or to
  share for a check-up.
