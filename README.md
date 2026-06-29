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

On a Mac (Apple Silicon — e.g. a Mac Mini M5):

```bash
git clone https://github.com/jeroenniesen/EnergyManagementSystem.git
cd EnergyManagementSystem
./scripts/install.sh        # or: make install
```

That single command bootstraps everything (no sudo, no Homebrew required): it installs `uv` if you
don't have it, fetches a local Node only if needed, builds the dashboard, sets up the Python
environment, installs a small auto-start service (it comes back after a reboot), starts the app, and
prints the URL. Then open **http://localhost:8080**.

- Run it in this terminal instead of as a service: `make dev` (`./scripts/install.sh --foreground`)
- Stop + remove the auto-start service (keeps your data): `./scripts/uninstall.sh` (`make uninstall`)

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
