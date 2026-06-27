# EV charge control — v2 specification (placeholder)

> **Status: not started — placeholder.** This is a deliberate stub so references from `../SPEC.md` (§2, §6.4, §16, §18) resolve. EV charge *control* is **out of scope for v1**; in v1 the Tesla is a **read-only, planned-around load** measured by the HomeWizard car meter (`../SPEC.md §6.4`). This document will be fleshed out before any EV-control work begins.

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
