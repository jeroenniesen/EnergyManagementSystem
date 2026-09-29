# Compatibility & positioning

> **Who this is for:** a prospective homeowner who wants a quick, honest answer to
> “does EMS fit my house, and why would I want it?”
>
> **What this is not:** a developer adapter guide (see [`adapters.md`](./adapters.md)) or the
> live-wiring runbook ([`live-integration.md`](./live-integration.md)). Hardware values tagged
> **untested** or **CONFIRM** below are hypotheses until verified on that device — the EMS fails
> safe to the battery’s own `AUTO` (self-consumption) when uncertain.

---

## Why EMS

EMS is a **mode-switching** home energy manager: it decides *which mode* the home battery should
be in (self-consumption, grid-charge to a target, hold reserve, discharge for load), a few times
an hour, and only commands the battery **when the desired mode changes**. It does **not**
continuously modulate live power.

| Season / strategy | What you get |
|---|---|
| **Summer / solar** | Fill from solar surplus so the house runs overnight on battery (+ a night reserve). |
| **Winter / arbitrage** | Charge at the daily price *dip* and discharge during price *peaks*, when the spread beats wear + risk. |
| **`auto`** | Picks the strategy from forecast surplus + price spread, not the calendar month. |

**Product promise (honest):**

- Ships in **simulation + dry-run** — never writes to the battery until you deliberately arm live
  control (yaml dry-run floor, Settings Watch only, operational toggle, live devices + live
  prices). See the README Safety section and [`control-model.md`](./control-model.md).
- Every plan passes a **validator** before it can move the battery; stale or uncertain data ⇒
  fall back to the battery’s own `AUTO`. The system aims to be **never worse than having no EMS**.
- Decisions carry a human-readable reason — including **why it is *not* acting**.
- Built-in web UI with graphs; optional AI explanations; optional iOS glance app (read-only).

If your home does **not** match the supported stack below, EMS is not a fit today — do not expect
a drop-in for arbitrary inverters, fixed tariffs, or charger control.

---

## At a glance

| Area | Supported today (shipped) | Not supported / not tested |
|---|---|---|
| **Battery** | Indevolt **SolidFlex 2000 Gen-2** (one tower or multi-tower cluster as one logical device), via local OpenData RPC | Other Indevolt models, other brands (Tesla Powerwall, Sonnen, Growatt, Victron, …), HA-mediated battery writes |
| **Meters** | **HomeWizard** P1 (`HWE-P1`) + kWh (`HWE-KWH1` / `HWE-KWH3`) for solar and optional car | DSMR P1 sticks without HomeWizard, Shelly / other brand meters, CT clamps alone |
| **Tariffs / prices** | **Tibber** dynamic day-ahead (GraphQL; hourly expanded to 15‑min planner slots) | EnergyZero, ENTSO-E, Frank Energie, ANWB, fixed/peak-offpeak-only contracts as a price source |
| **Solar forecast** | **Forecast.Solar** (keyless, default) · **Solcast** (API key + resource id) | Other forecast vendors; ML forecaster is optional / accelerator-gated and not required |
| **EV** | **Advice only** — car meter load + cheapest plug-in windows; winter planner may size for expected EV kWh | Controlling a wallbox, Tesla, or OCPP charger (v2 stub — [`v2-ev-control.md`](./v2-ev-control.md)) |
| **Inverters** | *Not controlled.* Solar is measured via the HomeWizard kWh meter + forecast; the inverter brand is irrelevant as long as production is metered | Hybrid inverter EMS modes, export-limit fights, vendor “smart” inverter scheduling |
| **Home Assistant** | Optional / **planned** hub path — `ems/sources/ha.py` is a **read-only skeleton**, not wired into sense or control | Required HA for live control on today’s Mac Mini path (it is **not** required) |
| **Host** | **Mac (Apple Silicon)** production today · **Raspberry Pi 5** / container target · optional **Jetson** ML sidecar | Untested: Windows-as-host, bare-metal non-Linux/macOS, public internet exposure |

**Verified live end-to-end** (one reference home, documented in [`live-integration.md`](./live-integration.md)): HomeWizard P1 + solar + car kWh, Tibber prices, Indevolt SolidFlex cluster SoC/power reads. Battery **writes** are implemented and unit-tested against a mock device; live write arming stays gated and opt-in.

---

## Battery

| Item | Detail |
|---|---|
| **Supported** | Indevolt **SolidFlex 2000**, **Gen-2**, latest firmware. One tower **or** a multi-tower cluster — the EMS treats the cluster as **one** logical device (commands go to the master). |
| **Interface** | Local **OpenData RPC** (`Indevolt.GetData` / `Indevolt.SetData`) on the LAN — shipped path in `ems/sources/indevolt.py` / `indevolt_driver.py`. |
| **Control model** | Mode-switching only (target &lt; ~10 writes/day, ≥5 s between writes). Intents: allow self-consumption · grid-charge to target · hold reserve · discharge for load. The Indevolt owns fast **P1 zeroing** in self-consumption — EMS does not fight it. |
| **Verified** | Read path (SoC, power, mode) live on a 2-tower ~10.8 kWh cluster. Write path implemented + hermetic tests; live production writes only after deliberate arming. |
| **Untested / unknown** | Other Indevolt SKUs (e.g. non-SolidFlex), Gen-1 behaviour, exact per-tower max W ceilings beyond probe/settings (**CONFIRM** on your hardware), whether P1 zeroing stays active in every physical mode (stored at capability probe — not assumed). |
| **Not supported** | Any non-Indevolt battery brand. No second battery writer. No continuous power-tracking loop. |

---

## Smart meters

| Role | Supported device | Notes |
|---|---|---|
| **Grid (net import/export)** | HomeWizard **P1** (`HWE-P1`) | P1 is **net grid flow**, not house load. House load is reconstructed: `grid + solar + battery_power`. |
| **Solar production** | HomeWizard **kWh** (`HWE-KWH1` / `HWE-KWH3`) | Production = magnitude of meter power; confirm which meter is solar at setup. |
| **EV / car load** (optional) | HomeWizard **kWh** (often 3-phase) | Used for load reconstruction, Insights, and EV advice — not for charger control. |
| **Gas** (optional) | HomeWizard P1 `total_gas_m3` | Fed into Insights CO₂ reporting when present. |

**Not supported as first-class adapters:** DSMR USB sticks alone, Shelly EM, Youless, generic Modbus meters, CT-only clamps. You *could* mirror values into something EMS already reads, but there is **no** shipped adapter for those brands.

**Limitation:** missing or stale meters degrade data quality; unsafe quality keeps the battery in `AUTO`.

---

## Tariffs & electricity prices

| Provider | Status | Role |
|---|---|---|
| **Tibber** (dynamic / day-ahead) | **Supported** — shipped `ems/sources/tibber.py` | Sole live price source. GraphQL `priceInfo` (hourly → 15‑min slots; quarter-hour responses accepted without double-expand). |
| Mock / demo prices | Supported for simulation | Keep dry-run on — EMS will not live-command the battery on synthetic prices. |

**Not supported:** EnergyZero, ENTSO-E direct, Frank, ANWB Energie, Octopus, fixed-price-only, or dual-rate peak/off-peak CSV imports as a planner price source.

**Limitations (honest):**

- Tibber `total` = energy + energy tax; it **may not include all grid/transport fees**. Config can add explicit import/export fee assumptions for economics — confirm against your bill.
- Arbitrage only runs when net benefit (avoided peak − charge cost / efficiency − degradation − risk margin) clears the bar; quiet days do nothing by design.
- Live battery control requires a **live Tibber** feed, not demo prices.

---

## Solar forecast providers

| Provider | Status | Notes |
|---|---|---|
| **Forecast.Solar** | **Default**, keyless | Needs location (map pin), tilt, azimuth, kWp. |
| **Solcast** | **Supported** (optional) | API key + rooftop resource id (Settings or env on first boot). Prefer **P50** for expected case, **P10** for commitments (grid-charge sizing / overnight guarantee). |
| Mock forecast | Simulation | Credential-free demo. |

**Not supported:** Open-Meteo / PVGIS / other vendor APIs as first-class forecast adapters.

**Limitation:** a days-old warm-start forecast is treated as stale for planning confidence; missing forecast degrades quality and can skip aggressive commitments.

---

## EV (advice only)

| Capability | Status |
|---|---|
| Measure car charging via HomeWizard kWh meter | **Yes** (optional meter) |
| Dashboard / iOS “best window to plug in” advice | **Yes** — schedule + manual SoC anchor; recommends windows, **never actuates** |
| Winter planner sizes battery plan for expected EV kWh | **Yes** (exogenous load estimate — advice/forecast bridge) |
| Never discharge the home battery into the car | **Yes** (car-guard) |
| Control Tesla / wallbox / OCPP / Charge Amps / … | **No** — deferred to v2 ([`v2-ev-control.md`](./v2-ev-control.md), still a placeholder) |

If you need automatic charger control today, EMS is **not** that product yet.

---

## Inverters & solar hardware

EMS does **not** talk to the PV inverter. Compatibility is via:

1. a HomeWizard kWh meter on the solar circuit (or equivalent production signal EMS already understands), and  
2. a forecast configured for your roof (kWp, tilt, azimuth, location).

Hybrid-inverter “EMS” modes, vendor export-limit APIs, and inverter scheduling are **out of scope**. Fighting the inverter’s own limiter is explicitly avoided.

---

## Home Assistant, MQTT, CO₂, AI (optional layers)

| Layer | Status |
|---|---|
| **Home Assistant as hub** | **Target** architecture (Pi deploy) — not required on today’s Mac Mini direct-device path. `ems/sources/ha.py` = read-only skeleton, **not wired** into sense/control. |
| **MQTT discovery / publish** | **Not shipped** (`paho-mqtt` not a dependency). |
| **Grid CO₂ intensity** | Insights **reporting only** — static NL factor by default; optional ElectricityMaps live key. Does **not** drive battery control. NED.nl not implemented (no suitable public blended-intensity endpoint at time of writing). |
| **AI explainer / chat** | Optional (`template` default · `external_llm` e.g. MiniMax). Off-device payload is minimal and redacted; never touches control. |
| **ML planner / forecaster** | Optional, **accelerator-gated** (not on a plain Pi). Falls back to rule-based; never bypasses the plan validator. |

---

## Host & deployment

| Host | Status |
|---|---|
| **Mac Mini (Apple Silicon)** | **Production today** — `scripts/install.sh` / bootstrap → LaunchAgent |
| **Linux** (incl. Raspberry Pi OS) | Installer + `systemd --user`; Pi + Docker Compose is the **documented target** |
| **Nvidia Jetson** | Optional GPU ML sidecar; HA may run elsewhere on the LAN |
| Docker image | Included for container deploys |

**Not a supported deployment:** exposing port 8080 to the public internet. Remote access model = LAN over VPN ([`remote-access.md`](./remote-access.md)).

---

## Will it work for *my* home? (checklist)

You are in good shape if **all** of these are true:

1. You have (or will buy) an **Indevolt SolidFlex 2000 Gen-2** home battery on the LAN with OpenData enabled.  
2. You have **HomeWizard** P1 (+ preferably solar kWh; car kWh if you want EV advice).  
3. You have a **Tibber** dynamic contract (or accept simulation-only until you do).  
4. You can run EMS on a **Mac or Linux** box on the same LAN (Pi/Mac Mini/NUC).  
5. You accept **mode-switching** automation with dry-run-first arming — not continuous inverter/battery power tracking, and not EV charger control.

If any of (1)–(3) is a hard no, wait for a future adapter epic or choose a different product — this page will stay honest rather than promise a matrix we have not built.

---

## Related docs

- [`live-integration.md`](./live-integration.md) — wiring real HomeWizard / Tibber / Indevolt  
- [`control-model.md`](./control-model.md) — intents, validator, ownership, dry-run  
- [`energy-model.md`](./energy-model.md) — signs and house-load reconstruction  
- [`adapters.md`](./adapters.md) — developer adapter invariants  
- [`v2-ev-control.md`](./v2-ev-control.md) — EV control placeholder (not started)  
- [`../SPEC.md`](../SPEC.md) — source of truth · [`../README.md`](../README.md) — install & safety  
