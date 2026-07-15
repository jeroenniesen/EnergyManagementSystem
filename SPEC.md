# Smart Energy Manager (HEMS) — Build Specification

> **Status:** **Implementation-ready draft** — design complete and reviewed; several device-specific values still need **M0/M1 hardware validation** (see the validation checklist below and the *Known uncertainties* table in §17). Treat anything tagged **CONFIRM@M1** as a hypothesis until probed on the real hardware.
> **Owner:** Jeroen Niesen
> **Target platform:** Raspberry Pi (CPU-only core) **or** Nvidia Jetson (adds the optional GPU ML layer); Home Assistant runs on the same host (Pi) or elsewhere on the LAN (Jetson). See §11, [`docs/jetson-deployment.md`](docs/jetson-deployment.md). **These remain the target — today's actual deployment is a direct-device macOS install with no HA; see the reality callouts in §5.2 and §11 (BACKLOG B-26).**
> **Goal:** A *mode-switching* home energy management system that smart-charges a home battery using a solar forecast and dynamic (Tibber) prices, so the house runs on battery overnight in summer and arbitrages cheap/expensive price windows in winter.
>
> *This single document is both the **plan** (what it will do and why, §1–§3, §8) and the **application spec** (architecture, modules, config, deployment, build plan, §5–§7, §9–§18). Supporting reference docs live in `docs/` (see §18).*

### Validation checklist (do these first — gates M0→M1)

Run through this before trusting any strategy. Each item has a home in §17 (*Known uncertainties*).

- [ ] **Tibber token works** — personal token created; `viewer.homes[].currentSubscription.priceInfo` returns `today`/`tomorrow`; quarter-hourly `priceInfoRange` returns data (see §6.2 for the exact placement caveat).
- [ ] **HA Indevolt actions/entities discovered** — run the **capability probe** (§6.5): which of `indevolt.charge` / `indevolt.discharge` exist, which **entities** back "standby"/"energy mode"/"discharge floor"/"grid charging", and their parameter ranges.
- [ ] **Cluster max charge/discharge read** from the live HA power sensors (or `Indevolt.GetData`) → set `max_charge_w` / `max_discharge_w`.
- [ ] **HomeWizard meters identified** — confirm which `product_type` is P1 vs the two kWh meters, and which kWh meter is **solar** vs **car**; confirm each meter's **sign convention** (§4).
- [ ] **Solcast account created** — free Hobbyist (new account = 10 calls/day); resource id noted; a single refresh owner chosen (§6.3).
- [ ] **NTP healthy** — the Pi's clock is synced (price/charge windows are time-critical, §11).
- [ ] **Calibration period run** — at least a few days of read-only logging so the load model and forecast-correction factor are seeded **before** any control is enabled (§4.4, §14).

---

## 1. What this system does (in one paragraph)

The Smart Energy Manager ("EMS") is a small Python service that decides, a few times per hour, **which mode the home battery should be in** — charge, discharge, hold, or self-consumption — based on (a) a free forecast of tomorrow's/today's solar production, (b) the dynamic Tibber day-ahead electricity prices, (c) the current battery state of charge, and (d) the **reconstructed house load** (derived from the HomeWizard meters — *not* read directly off any one meter; see §4). It does **not** continuously modulate battery power (the Indevolt API is not designed for that); instead it computes a **plan** — a schedule of battery modes for the next 24–36 hours — and only issues a command to the battery **when the mode needs to change**. Everything is observable in its own web UI (and optionally Home Assistant), and every decision is explainable ("charging now because the cheapest 3 hours are 02:00–05:00 and forecast solar tomorrow is only 4 kWh") — including *why it is **not** acting* when it holds.

---

## 2. Design goals & non-goals

### Goals
- **Explainable & configurable.** You always know *what* it will do and *why* — including why it is **not** charging/discharging right now. Strategy lives in a single well-commented config file.
- **Mode-switching, not power-tracking.** Respect the Indevolt API: change mode infrequently (target: a handful of writes per day), never a tight control loop. A **minimum dwell time** per mode backs up the per-day cap.
- **Indevolt owns P1 zeroing — don't fight vendor control.** When paired with the P1 meter the battery runs its **own** fast self-consumption controller (modulating power to keep grid flow ≈ 0). The EMS sets *intent/mode* and lets that controller do the instantaneous tracking; it **never** repeatedly corrects minor live-power deviations. The EMS outputs a high-level **`BatteryIntent`** ("allow self-consumption", "grid-charge to target", "hold reserve", "discharge for load"), not low-level power behaviour (§7). *Whether P1 zeroing stays active in each mode is hardware behaviour we **verify and store at M1**, not assume (§6.5, §17).*
- **Two seasonal strategies** that switch automatically (or manually), with **hysteresis** so the strategy does not flip daily around the threshold:
  - **Summer:** charge the battery from solar surplus during the day so the house runs the *full night* on battery (+ a configurable reserve).
  - **Winter:** buy electricity at the daily price *dip* and discharge it during price *peaks* (price arbitrage), because solar is too small to fill the battery.
- **Free data only** for forecasting and prices (no paid subscriptions required to run the core).
- **Runs on a Raspberry Pi**, survives reboots, recovers cleanly, **fails *safe*** (if unsure, fall back to the battery's own self-consumption mode). The system must never be worse than "no EMS".
- **Economically honest.** Arbitrage is only taken when the spread beats round-trip losses **plus** a degradation allowance **plus** a risk margin **plus** any grid fees — not a fixed magic number (§8.3).

### Non-goals (v1 — YAGNI)
- No second-by-second power optimisation / no model-predictive control loop.
- **No automatic EV charging *control* in v1** — the Tesla is **read-only** input (its charging is a load we plan around, measured by the HomeWizard car meter). EV charge *control* is deferred to a **separate v2 specification** ([`docs/v2-ev-control.md`](docs/v2-ev-control.md), currently a placeholder stub), because it carries its own auth, safety, and UX complexity (§16).
- No selling/trading optimisation beyond simple price-window arbitrage.
- **No ML in the *core* path.** The baseline always runs without ML: a rolling historical average for consumption and the rule-based planner. An **optional ML layer** (learned load forecasting, a learned planner, and a local-LLM explainer) is a **documented, GPU-gated Jetson extension** — *additive, never required, and it never bypasses the safety layer* (§8.11). It is selected by a runtime **planner-mode switch** (`rule_based` | `ml` | `advisory`) and specified in [`docs/ml-layer.md`](docs/ml-layer.md). On a plain Pi it is simply off.

---

## 3. Hardware & data sources (your setup)

| Asset | Role in the system | Local API | Direction |
|---|---|---|---|
| **Indevolt home battery, ≈10.8 kWh** | The thing we control | Local API — *mode switching* | **Read + Write** |
| **Solar roof, 3 kWp (3000 Wp)** | Generation, measured + forecast | via HomeWizard kWh meter + Forecast service | Read |
| **Tibber dynamic contract** | Day-ahead prices (15-min) | Tibber GraphQL API (cloud) | Read |
| **Tesla Model Y** | A large, shiftable load to plan around | HomeWizard car meter (v1); Tesla Fleet / BLE (v2 only) | Read (v1) |
| **HomeWizard P1 meter** | **Net grid import/export** (NOT house load — see §4) | HomeWizard local API | Read |
| **HomeWizard kWh meter — Solar** | Actual solar production | HomeWizard local API | Read |
| **HomeWizard kWh meter — Car** | Actual EV charging power | HomeWizard local API | Read |
| **Home Assistant** | Integration hub + dashboards + history | HA WebSocket/REST API + MQTT | Read + Write |
| **Raspberry Pi** | Runs HA + the EMS service | — | — |

---

## 4. Energy & measurement model (read this before trusting any number)

> This section did not exist in earlier drafts and is the single most important correctness fix: **the P1 meter is *net grid flow*, not house load.** Treating it as house load corrupts every downstream calculation. The full data dictionary is in [`docs/energy-model.md`](docs/energy-model.md).

### 4.1 Sign conventions (fixed, EMS-internal)
We normalise every source to **one** internal convention and confirm each source's native sign during calibration (§4.4):

| Internal metric | Sign convention | Native source |
|---|---|---|
| `grid_power_w` | **+ = import** (drawing from grid), **− = export** (feeding grid) | P1 `active_power_w` |
| `solar_power_w` | **≥ 0** = production | solar kWh meter (magnitude) |
| `battery_power_w` | **+ = discharge** (battery → house), **− = charge** (into battery) | Indevolt sensor (normalised) |
| `ev_power_w` | **≥ 0** = EV charging load | car kWh meter |
| `soc_pct` | 0–100 % | Indevolt sensor |

### 4.2 Reconstructed house load (the key formula)
House load is **derived**, never read directly:

```
house_load_w     = grid_power_w + solar_power_w + battery_power_w      # full house demand
non_ev_load_w    = house_load_w − ev_power_w                           # what the planner learns
```

Sanity-check the convention with cases (all should yield 1000 W of true house demand): grid-only `(+1000,0,0)`; solar-covering `(−500,1500,0)`; battery-covering `(+200,0,+800)`; charging-from-grid `(+1500,0,−500)`. If any case is off, the sign of a source is wrong — fix it in calibration, not in the planner.

### 4.3 Raw vs. derived storage
The history store keeps **raw measurements and derived values in separate columns/tables** so we can always re-derive after fixing a sign or calibration error:
- **Raw:** `grid_power_w`, `solar_power_w`, `ev_power_w`, `battery_power_w`, `soc_pct`, plus per-source meter totals (`*_import_kwh`, `*_export_kwh`) and the P1 gas meter (`total_gas_m3`). Battery/solar power are **plausibility-clamped on ingest** (implausible spikes clamped to a sane bound and flagged in recorder health) so one bad reading can't poison the store.
- **Derived:** `house_load_w`, `non_ev_load_w`, learned baseline, forecast-correction factor, projected SoC, computed savings.
- **Observability (append-only, for after-the-fact validation):** `forecast_snapshots` (day-ahead P10/P50/P90 per 15-min slot, snapshotted at issue time so forecast **lead-time is preserved**, not decayed to a nowcast), `plan_history` (per-cycle strategy / target-SoC / deadline / achieved-SoC / intent), and `gas_readings` (cumulative `total_gas_m3`). Each is purged on the same `history.retention_days` schedule as the sample tables.

### 4.4 Calibration phase (before control is enabled)
Historical HA Recorder data already includes the battery's *prior* behaviour, so the learned baseline must be reconstructed, not read raw. Before enabling any writes (gate M0→M2):
1. Log raw + derived values read-only for a few days (§14 dry-run/calibration milestone gate).
2. Verify sign conventions against the cases in §4.2.
3. Seed the **load baseline** (§8.1) and the **forecast-correction factor** (§6.3) from this window.

### 4.5 EV exclusion (precise rule)
Subtract the HomeWizard car meter from the learned baseline **only when the car is actually charging** (`ev_power_w` above a small threshold). When the car is not charging, it contributes nothing and is not subtracted. The planner then re-adds expected EV load as a *separately known* quantity when it knows the car will charge.

> **Implemented — don't feed the car from the home battery, three operator-chosen behaviours (feat/car-charge-modes, CCM-1/CCM-2).** A real-time guardrail (`_car_guard` in `ems/web/api.py`, pure decision core `ems/control/car_mode.py`): while the car is charging (`ev_power_w > control.car_charging_threshold_w`) and the master switch `control.hold_battery_when_car_charging` is on (default), the guard resolves the discharge-shaped intents (`ALLOW_SELF_CONSUMPTION`/`DISCHARGE_FOR_LOAD`) to whichever behaviour `control.car_charging_battery_mode` selects:
>  - **`hold`** (default) — today's guard, **byte-for-byte unchanged**: forced to `HOLD_RESERVE` → `IDLE`. The battery holds (and may still charge from solar surplus / a planned grid-charge); solar + grid cover the car.
>  - **`static_discharge`** — the battery discharges at a fixed `control.car_discharge_w` (100–5000 W, clamped to `max_discharge_w`); any part of that **above** the actual predicted house load DOES flow to the car from the battery — a deliberate, honestly-surfaced choice (reason string names the physics), not a guard failure.
>  - **`match_home_load`** — the battery discharges at the **predicted non-EV house load** (trailing same-weekday-hour observation mean, profile fallback, floored 150 W / capped 3000 W / rounded to 50 W — `predict_house_load_w`), so it quietly covers the house while the grid keeps feeding the car and the battery never ends up feeding the car itself.
>
>  Both discharge behaviours route through the **same single writer** (`ems/sources/battery.py`) under the narrow `car_session` mapping (§7.1): `intent_to_mode(DISCHARGE_FOR_LOAD, car_session=True)` forces a real `DISCHARGE` at the bounded setpoint even when `allow_export_discharge` is off — accepted because the setpoint is clamped to ~the house load (not an export dump) and the session re-evaluates every cycle, so a stopped car ends the `DISCHARGE` within one cycle (the only export exposure is that one-cycle window, accepted and documented). Both discharge modes still honour the **inviolable reserve floor** (`min_reserve_soc` + 1pp hysteresis) — near the floor the guard falls back to `hold` regardless of the chosen mode, and unsafe data quality suppresses a discharge to `hold` (an untrusted SoC never drives one).
>
>  **Write-bounds keep this a mode-switch, never a power-tracking loop (§2/CLAUDE.md):** the battery is (re-)commanded only at session start or when the setpoint drifts more than `rebond_threshold_w` (500 W — the **recommand rule**, `ems/control/car_mode.py`), gated further by a **≥10-minute minimum command dwell** and a **hard 6-command cap per session** (`_decide_car_command` in `ems/web/api.py`) — a noisy multi-hour session costs a handful of writes, not one per cycle; a write timeout/unconfirmed command HOLDs rather than reverts (established pattern, §6.5).
>
>  **Defaults are unchanged** — `car_charging_battery_mode` defaults to `hold`, so an upgraded install behaves exactly as before until the operator opts into a discharge mode. This remains the final guardrail in `_effective_intent` (over the plan and a manual override) and is re-evaluated **every control cycle**, so it engages the moment the car plugs in and releases when it stops. All three settings are edited from the Car tab's "While the car charges" section (moved out of the Settings "Control" group); the dashboard's car badge now reflects which behaviour is active instead of always reading "battery held".

### 4.6 Missing / stale meter fallback
Per-source freshness is tracked (§6, §16). If a meter is missing or stale:
- **Solar meter stale** → fall back to the solar *forecast* for `solar_power_w` (flag the chart).
- **Car meter stale** → assume `ev_power_w = 0` and widen the load uncertainty band.
- **P1 stale** → reconstruction is unreliable → **fail safe to `AUTO`** and raise an alert (§9.3).
- Any reconstruction using a stale input is flagged in the UI freshness indicators (§9.1).

### 4.7 Data quality (per-signal staleness, plausibility, source priority)
Quality is tracked **per signal, not as one global stale flag** ([`docs/control-model.md`](docs/control-model.md) §11):
- **Per-signal staleness:** each of `grid`/`solar`/`ev`/`soc`/`price`/`forecast` has its **own** freshness state and age, surfaced individually in the UI (§9.1) and feeding the per-plan data-quality badge (§8.11).
- **Source priority per metric:** **HA sensor → direct device API → cached value** — and a *cached* value is for **display only, never for control**.
- **Plausibility checks:** reject/flag implausible readings — SoC can't jump more than `soc_max_jump_pct_per_5min` (e.g. 20%/5 min), `solar_power_w` can't be negative, prices must be **chronological** and within sane bounds.
- **Timestamp hygiene:** dedupe 15-min slots by `startsAt`; fill or flag missing slots; **never silently shift slots** (DST-correct). All slot math uses **tz-aware** datetimes (§13.1).

---

## 5. Architecture

### 5.1 The big picture

```
   Cloud APIs                  Raspberry Pi 5                        You
  ┌────────────┐     ┌─────────────────────────────────────┐    ┌──────────┐
  │ Tibber     │     │ Home Assistant (hub / integrations) │    │ Browser  │
  │ Solcast    │◄───►│ Tibber · HomeWizard · Forecast.Solar│    │ / phone  │
  │ Forecast.S │     └─────────────────┬───────────────────┘    └────┬─────┘
  └────────────┘                       │ WS/REST + MQTT              │ HTTP/WS
                                       ▼                             ▼
   Local network            ┌───────────────────────────────────────────────┐
  ┌──────────────┐   read   │ EMS CORE  (Python · FastAPI)                   │
  │ HomeWizard   │◄─────────┤   Forecaster · Planner · Mode Controller       │
  │ P1 + kWh ×2  │ (opt.)   │   Web UI (graphs)  ·  SQLite history store      │
  └──────────────┘          └────────────────────┬──────────────────────────┘
  ┌──────────────┐  mode (infrequent)            │
  │ Indevolt     │◄──────────────────────────────┘
  │ SolidFlex ×2 │   via HA indevolt actions/entities (or local OpenData RPC)
  └──────────────┘
```

### 5.2 Two-layer split (the key decision)

- **Home Assistant = the integration hub & system of record.** It already has (or can have) battle-tested integrations for Tibber, HomeWizard, Forecast.Solar, Solcast and Tesla. It handles auth, polling, retries, history, and dashboards. We do **not** reinvent device drivers.
- **EMS Core = a small standalone Python (FastAPI) service** that holds *all the decision logic* **and serves its own web UI**. It reads sensor state from HA (WebSocket/REST) and writes its decisions back to HA (and/or directly to the battery's local API), records its own time-series to a local **SQLite** store, and exposes a **browser dashboard with graphs** (§9.1).

**HA is required for live operation; the UI degrades gracefully without it.** Be precise about the dependency (this corrects an over-claim in earlier drafts):
- **Live telemetry & control require HA** (it owns the device integrations and the `indevolt.*` action/entity surface). If HA is down, the EMS **cannot read fresh state or command the battery** → it fails safe to leaving the battery in `AUTO`.
- **The dashboard survives an HA outage** by serving from its **own SQLite history** — you can still inspect the last plan, decisions, and historical graphs. But it is read-only/stale during the outage, not "fully operational".

**Read from HA, optional direct-device fallback — decided per source:**
| Source | Primary read | Direct fallback | Default |
|---|---|---|---|
| HomeWizard P1 + kWh ×2 | HA sensors | HomeWizard local API | HA (fallback configurable) |
| Tibber prices | **EMS → Tibber GraphQL directly** (HA doesn't expose the arrays) | EnergyZero/ENTSO-E via HA | direct |
| Solar forecast | EMS → Solcast directly *or* HA HACS Solcast | Forecast.Solar keyless | **EMS owns refresh** (§6.3) |
| Battery read/write | **HA `indevolt.*`** actions/entities | OpenData RPC | HA (probe-decided, §6.5) |

> **Implemented reality (BACKLOG B-26 reconcile, PRs #13–#24 status).** No HA client (`ems/sources/ha.py`) exists, and the shipped `config.yaml` has no `homeassistant:` block or `entity_map` — every source above ships only its **fallback** column today, read/written **directly**: HomeWizard local v1 API (`ems/sources/live.py`), Tibber GraphQL direct (as always designed), solar forecast via **Forecast.Solar only** (Solcast not built — §6.3), and the Indevolt battery via **direct OpenData RPC only** (`ems/sources/indevolt.py` / `indevolt_driver.py` — no `indevolt.*` HA actions — §6.5). Home Assistant is **not required** to run the EMS; nothing in the codebase reads or writes it. HA integration remains the target architecture (tracked as **BACKLOG B-18**, pool, not scheduled) — treat every "HA"/"via HA" cell above, and §9's `homeassistant:`/`mqtt:` config blocks and §9.2 (MQTT/HA entities), as **planned, not yet implemented**.

**Entity mapping config (don't rely only on auto-discovery names).** HA entity ids vary by install (`sensor.p1_meter_active_power` vs `sensor.homewizard_p1_power`, etc.). The EMS keeps an **explicit `entity_map` in config** (§9) that maps internal roles → HA entity ids, seeded by discovery but pinned in config so a rename in HA can't silently break reconstruction. A startup validation step checks every mapped entity exists with a sane `state_class`/unit (§11.5).

**Why standalone Python (not AppDaemon / pure HA automations)?** Same rationale as before: the planning logic is real code with unit tests; the service runs and is reasoned about independently of HA; if HA restarts the EMS keeps its plan; if the EMS crashes the battery falls back to its own safe mode. *Alternative considered:* **AppDaemon** (kept as a fallback option, §13) — same decision logic, different host, but you lose the self-contained web UI.

### 5.3 Data flow each control cycle (every 5 minutes)
1. **Sense** — read raw meter values, **reconstruct** house load (§4), read SoC, current price, cached forecast/plan; stamp freshness per source.
2. **Plan (if stale)** — once or twice a day (and on big deviations), rebuild the 24–36 h mode schedule **including a projected-SoC curve** (§8).
3. **Decide** — look up "what mode should I be in *right now*" from the plan; compute the **reason**, including the no-action reason.
4. **Act (only on change, only if dwell satisfied, only if fresh)** — if desired mode ≠ battery's current mode, the per-day cap and min-dwell allow it, and data is fresh → send one mode-switch command, then **confirm it** (§6.5). Otherwise do nothing.
5. **Publish** — push current mode, plan, reasoning, forecast, **and freshness/alerts** to the UI and (optionally) HA via MQTT.

---

## 6. Component integration details

> Verification status is **per integration**, not a blanket "verified June 2026". Public API shapes (Tibber GraphQL, HomeWizard local API, Forecast.Solar) are verified against vendor docs; **device-/account-/firmware-specific values are runtime-specific** and tagged **CONFIRM@M0/M1** — they live in the §17 uncertainties table with an owner and the evidence required.

### 6.1 HomeWizard (P1 + 2× kWh meters) — read-only telemetry
- **Local HTTP API**, no cloud needed. Discover devices via mDNS (`_hwenergy._tcp.local`). `GET http://<ip>/api` returns `product_type` — branch your code on this, and **target one API version per device** (v1 and v2 field names differ).
- Your three meters: the **grid meter is a P1 (`HWE-P1`)** → `grid_power_w` (**net**); the **solar and car meters are kWh meters (`HWE-KWH1`/`HWE-KWH3`)** → `solar_power_w` / `ev_power_w`. **CONFIRM@M0** which kWh meter is which, and each meter's native sign.
- **v1 (legacy, token-less):** measurements at `GET http://<ip>/api/v1/data`. P1 also exposes the raw DSMR telegram at `GET /api/v1/telegram`.
  - **P1**: `active_power_w` (signed: + import / − export), `active_power_l1/l2/l3_w`, `total_power_import_kwh` (+ `_t1/_t2` tariffs), `total_power_export_kwh`, voltages/currents, gas (`total_gas_m3`).
  - **kWh meter**: `active_power_w` (+ per-phase), `total_power_import_kwh`, `total_power_export_kwh`, voltage/current/power-factor.
- **v2 (current, recommended for new builds):** HTTPS + **bearer token**. Get a token once: press the device button, then `POST https://<ip>/api/user {"name":"local/ems"}`; send `Authorization: Bearer <token>` thereafter. Measurements at `GET https://<ip>/api/measurement`. v2 **drops prefixes** (`power_w`, `energy_import_kwh`).
- **Polling:** no hard limit, but **do not poll faster than every 500 ms**; power refreshes ~1–60 s, gas every 5–60 min. We poll every few seconds — or read HA's HomeWizard sensors.
- **In HA:** the official **HomeWizard Energy** core integration auto-discovers via mDNS and exposes all of this as sensors. Simplest path: consume those (with the `entity_map`, §5.2). **Missing/stale meter behaviour:** §4.6.

### 6.2 Tibber (dynamic prices) — queried directly by the EMS
- **GraphQL API:** `POST https://api.tibber.com/v1-beta/gql`, header `Authorization: Bearer <personal-token>` (from `developer.tibber.com/settings/accesstoken`).
- **Hourly prices:** `viewer.homes[].currentSubscription.priceInfo` → `current`, `today[]`, `tomorrow[]`; each `Price`: `total` (energy+tax), `energy` (Nord Pool spot), `tax`, `startsAt` (ISO-8601), `level`, `currency`. **`today`/`tomorrow` are *hourly*.**
- **15-minute prices (NL, since 1 Oct 2025) — CORRECTED placement.** `priceInfoRange` is **nested under `currentSubscription`** (it is a field of `Subscription`/`PriceInfo`), **not** a top-level `viewer` query as earlier drafts stated. It takes a **required `resolution`** (`QUARTER_HOURLY` | `HOURLY` | `DAILY`) plus pagination (`first`/`last`/`before`/`after`), and is **capped at 672 quarter-hours (7 days) / 744 hours (31 days) / 31 days**. The exact query shape lives in [`docs/api-reference.md`](docs/api-reference.md). The planner works in 15-min slots and **degrades to hourly** (§6.2 fallback).
- **Store both resolutions, normalise to 15-min slots.** Cache `today`/`tomorrow` (hourly) **and** the quarter-hourly range when available; the planner's internal unit is the **15-min slot**.
  - **Fallback expansion (hourly → quarter-hourly):** if quarter-hourly is unavailable, **expand each hourly price into four identical 15-min slots**. Mark these slots `resolution=hourly` so the UI can show they're coarse.
- **Caching (prices don't change retroactively).** Tibber states historical prices are immutable, so once fetched, **persist each slot to SQLite and never re-fetch a past slot.** Only fetch forward (today's remainder + tomorrow). This makes the planner robust to Tibber outages and cuts API load.
- **Completeness validation before planning.** Before a winter replan, assert tomorrow's array is **complete** (expected slot count for the date, accounting for DST — 96/92/100 quarter-hours on a normal/spring-forward/fall-back day). A partial array → **do not plan on it**; keep the prior plan or fall safe (§16 freshness rules).
- **Tomorrow's** prices appear around **13:00 CET**, tied to the EPEX day-ahead auction — the trigger to (re)build the next-day plan. The endpoint is congested at 13:00; **poll with a few minutes of random jitter** (retry 13:00–14:00).
- **Freshness rules (explicit):** a price set is *fresh* if its slots cover **now → end-of-known-horizon** with no gaps; *stale* if tomorrow hasn't arrived by a configurable cutoff (e.g. 15:00) **and** today's remaining slots are exhausted. Fallback priority: cached Tibber slots → live Tibber → EnergyZero/ENTSO-E cross-check → `AUTO`. Partial arrays are **not** silently accepted.
- **Negative prices & export tariffs.** Handle explicitly: negative `total` is valid and changes strategy (charging may be *paid*, exporting may *cost*). The economics model (§8.3) uses signed prices and a configurable **export tariff/feed-in policy** rather than assuming export is always free or always valued at spot.
- **Grid fees / taxes policy.** Tibber `total` = energy + energy tax, but **does not necessarily include all transport/grid-operator fees** that affect true import/export economics. Config carries an explicit `grid_fees` policy (§9): whether Tibber `total` is sufficient or a fixed `import_fee_eur_per_kwh` / `export_fee_eur_per_kwh` should be added. **CONFIRM@M0** what your tariff actually charges.
- **Why query Tibber directly (not via HA):** HA's Tibber integration only exposes the *current* price (+ today's min/max as attributes); it does **not** expose the full arrays cleanly. The EMS needs the whole curve.
- **Free fallback/cross-check (no key):** HA **EnergyZero** core integration (NL day-ahead hourly, tomorrow ~14:00) + `get_energy_prices` action; **ENTSO-E** (HACS, free token) or **Nord Pool** alternatives. Used as **cross-check / outage fallback only**, not as the default provider unless you deliberately switch.

### 6.3 Solar production forecast (free) — Solcast primary, Forecast.Solar fallback
All options below are free; one **primary** + one **fallback**, PVGIS once for a baseline.

- **Forecast.Solar (free public, no key)** — simplest. `GET https://api.forecast.solar/estimate/{lat}/{lon}/{tilt}/{azimuth}/{kwp}` → `watts`, `watt_hours_period`, `watt_hours_day`. **Limits: rate-limited (≈12 calls/hour per IP), 1 plane, today+tomorrow, hourly** — i.e. a *simple, rate-limited* fallback, **not** "uncapped" (correcting an earlier config comment). Raw-API azimuth: **0 = South**, −90 = E, +90 = W. **HA core "Forecast.Solar" integration** is keyless — enter **3000** Wp; **HA UI azimuth gotcha: due-south = 180** in the HA UI (but `0` in the raw URL).
- **Solcast Hobbyist (free, best accuracy)** — satellite nowcasting, **P10/P50/P90** percentiles. Limits: **10 API calls/day** (new accounts; older keep 50), 1 site (up to 2 azimuths), **7-day, 30-min**. `GET https://api.solcast.com.au/rooftop_sites/{resource_id}/forecasts?format=json` → `pv_estimate` (kW, P50), `pv_estimate10/90`, `period_end`. HACS integration **`BJReplay/ha-solcast-solar`**.
- **Open-Meteo** — kept as a **documented optional fallback only** (out of scope for the core build; add only if Forecast.Solar proves insufficient). `global_tilted_irradiance` w/ `tilt`/`azimuth` (0 = south); `PV_kWh = GTI/1000 × kWp × PR` (PR ≈ 0.80). ~10–15% error.
- **PVGIS (free, no key)** — *not* a forecast; call **once** for expected annual/monthly yield to sanity-check the system and **calibrate `summer_solar_threshold_kwh`** to your roof (§8.4).

**Chosen setup & ownership (single decision, no ambiguity):**
- **Solcast Hobbyist is primary**, **Forecast.Solar (keyless) is the automatic fallback** when Solcast is stale/unreachable.
- **The EMS owns the Solcast refresh** (default) — it keeps a **call-budget ledger** (§6.3 below) so accidental refresh loops cannot exhaust the 10/day budget. (You *may* instead let the HACS integration own it and have the EMS read the sensor — but pick **one** owner; the default is EMS.)
- **Solcast call-budget ledger.** Persist a daily counter (resets at local midnight) of Solcast calls. Refresh only on a fixed daylight schedule (e.g. `07:00,09:00,11:00,13:00,15:00,17:00,19:00` = 7/day, 3 spare), and **refuse** a refresh that would exceed `solcast_daily_call_budget`. A retry/backoff bug therefore can't burn the budget.
- **Store forecast provenance.** Each forecast record stores its **issue time** and **provider** (`solcast` | `forecast_solar`). The planner knows whether it is using a fresh Solcast forecast or a stale/fallback one, and the UI shows it.
- **Rolling, bounded correction factor.** Calibrate against the **actual solar kWh meter**: maintain a rolling correction factor `corrected = raw_forecast × k`, updated from recent forecast-vs-actual ratios. **Clamp `k` to `[0.7, 1.3]`** (`forecast_correction_bounds` in config) so one anomalous day can't poison the model.
- **P10/P50 by purpose:** **P50** for the *expected* case (display, summer sizing); **P10** (pessimistic) for *commitments* that would otherwise risk a shortage (winter grid-charge sizing, summer overnight guarantee). **P90** only for "how much surplus might I have to export".

> **Implemented reality:** only `ForecastSolarSource` (`ems/sources/forecast_solar.py`) ships. Solcast integration is **planned, not yet implemented** (BACKLOG B-14, pool) — `forecast_provider: solcast` in the §9 sample is aspirational; the shipped default (and only) provider is `forecast_solar`. P10/P90 bands are **derived** from the Forecast.Solar P50 via fixed multipliers (≈0.6×/1.15×), not real percentiles from a provider, until B-14 ships.

### 6.4 Tesla Model Y (read-only in v1; control deferred to a v2 spec)
- **v1:** we only need **how much the car is drawing now** so the planner treats EV charging as a known load — the **HomeWizard "car" kWh meter measures that directly**. v1 needs **no Tesla credentials at all**. Optionally read SoC/plugged-in state for nicer planning; not required.
- **v2 (separate spec — `docs/v2-ev-control.md`).** Controlling charging (BLE via `yoziru/esphome-tesla-ble`, Tessie ~$13/mo, Teslemetry, or Tesla Fleet API with command signing + self-hosted HTTP proxy) carries its own auth, safety bounds (amps/limit min-max undocumented, read at runtime), rate limits (waking the car burns credits), and debounce requirements. **It is intentionally *not* folded into M5 of this spec** — it gets its own document so this HEMS spec stays focused (§16). Until then EV is a planned-around load via the HomeWizard car meter.

### 6.5 Indevolt battery (the controlled device) — corrected command surface + capability probe
Indevolt is a German brand (Power Genius GmbH). Your system is a **SolidFlex 2000 (Gen-2), two towers in a cluster (≈10.8 kWh total), latest firmware**, with a local **"OpenData" API**.

> **Cluster note:** control the cluster as a *single* logical device — one command applies to the whole cluster. Combined inverter power ≈ **~2 kW/tower → ~4 kW** — **CONFIRM@M1** the exact ceiling from HA power sensors / `Indevolt.GetData` (drops if you set a feed-in/output limit).
>
> **Implemented (read):** each tower reports its **own** SoC + rated capacity, so the system SoC is the **capacity-weighted average** across all configured towers, and power is their signed sum (`ems/sources/indevolt.py` `IndevoltClusterReader`; aggregation + fail-safe in [`docs/energy-model.md`](docs/energy-model.md) §9). Confirmed live: master `…53` 5.38 kWh + slave `…22` 5.60 kWh ⇒ **10.98 kWh**. The master (`battery.indevolt_ip`) is the write target; additional towers are listed in `battery.indevolt_ips_extra` and shown per-tower in the UI.

**The command surface is *probed*, not assumed (corrected).** The official HA integration (repo `INDEVOLT/homeassistant-indevolt`) provides **fewer services than earlier drafts claimed**. Verified against the HA docs:
- **Services that exist:** `indevolt.charge` and `indevolt.discharge` (both run *until a target SoC*; treat `power` + `target_soc` as candidate params and **confirm at probe**).
- **There is NO `indevolt.stop` service and NO `indevolt.change_mode` service.** Instead:
  - **Standby / idle hold** is a **button entity** ("Enable standby mode"), not a service.
  - **Self-consumption / mode** is a **select entity** ("Energy mode"), not a service.
  - **Discharge floor (min SoC)** = a **number entity** ("Discharge limit").
  - **Max AC output power / feed-in limit / inverter input limit** = **number entities** (Gen-2).
  - **Grid charging** = a **switch entity** ("Allow grid charging").

> **Implemented reality:** only **(B) direct OpenData RPC**, below, ships (`ems/sources/indevolt.py` read, `ems/sources/indevolt_driver.py` write) — path (A)'s whole HA capability-probe-via-services flow is **planned, not yet implemented** (§5.2, BACKLOG B-18). The real device needs **no auth at all** in practice — plain HTTP POST, IP-only, no key — contrary to the "Auth: HTTP Digest" line in (B) below (never observed/required against the live device; correct only if a future firmware demands it). The M1a `probe()` **is** real — it reads capacity/mode/meter-pairing live over RPC — but most `CapabilityReport` fields (`services`, `energy_mode_options`, `has_standby`, `has_grid_charge_switch`) are **fixed constants** in code, not dynamically discovered from HA service/entity schemas as designed below.

**A) Primary — via HA, after a capability probe (M1a).** At startup the EMS runs a **capability probe** and records a stored **`CapabilityReport`** (full schema in [`docs/control-model.md`](docs/control-model.md) §6):
1. List available `indevolt.*` services and their schemas.
2. List Indevolt entities: the **energy-mode select** and its selectable options, the **standby button**, the **discharge-limit number**, **max-power/feed-in numbers**, the **grid-charging switch**, and SoC/power **sensors** (incl. observed min/max power).
3. Confirm whether a *true IDLE/hold* exists (standby button) or must be **emulated** (§7.2), and whether **standby/hold is distinct from "self-consumption disabled"**.
4. **Paired-meter check:** is the Indevolt actually **reading the P1 meter** (P1 zeroing possible)? Store `p1_paired`.
5. **P1-zeroing by mode:** verify and store **whether P1 zeroing stays active in each mode** (`AUTO`/`CHARGE`/`DISCHARGE`/`IDLE`) — this is the contract from §2/§7.1, **measured at M1**, not assumed.
6. **Capture the battery's original vendor mode** so the EMS can **restore it** on shutdown / "return to default" / pause (§13.3, §9.1).
7. **Detect a pre-existing vendor schedule / manual mode:** if the battery is already in its own ToU schedule or a manual mode, decide per `battery.takeover_policy` whether the EMS may override it or should stand down (default: **don't override an active vendor schedule without explicit opt-in**).

`battery.py` then **builds its mode→action mapping from the `CapabilityReport`**, choosing **HA-action mode** when the needed surface exists and falling back to **RPC mode** (or emulation) otherwise (§6.5-B).

**B) Fallback — direct OpenData RPC** (enable the local API in the Indevolt app first).
- `POST http://<ip>:8080/rpc/Indevolt.GetData` (read), `…/Indevolt.SetData` (write), `…/Sys.GetConfig`. Auth: **HTTP Digest** (user `opend` + device key).
- **Mode** = data point `47005`: `1` self-consumption · `4` real-time control · `5` ToU schedule · `0` outdoor.
- **Explicit control** inside real-time (4), write **together**: `47015` state (`0` idle/hold, `1` charge, `2` discharge), `47016` power W (**≈50–2400, model-dependent — read real max from `GetData`**), `47017` target SoC (5–100 %).

**The mode→action mapping the EMS uses (probe-resolved):**
| EMS mode | HA path (primary, probe-resolved) | RPC (fallback: 47005 / 47015) |
|---|---|---|
| `AUTO` | set **energy-mode select** → self-consumption option | 47005=1 |
| `CHARGE` | `indevolt.charge {power, target_soc}` | 47005=4, 47015=1, 47016=W, 47017=SoC |
| `DISCHARGE` | `indevolt.discharge {power/target_soc}` — **deliberate export only**; serving house load uses `AUTO` (§7.1, §8.3) | 47005=4, 47015=2, 47016=W |
| `IDLE` (hold) | **standby button** if it truly holds SoC; else **emulate** (§7.2) | 47005=4, 47015=0 |

**Control hygiene (new, fail-safe):**
- **Min dwell per mode** (`min_mode_dwell_seconds`, e.g. 600 s) in addition to `max_mode_switches_per_day` — backs up the write cap and prevents flapping (§8.8).
- **Idempotency.** Never resend a command if the battery's *current* state already matches the desired mode. Only (re)write when observed state contradicts intent.
- **Export gating.** Serving house load during expensive windows is done via **self-consumption (`AUTO`)**, not forced discharge (§8.3). The EMS issues a **forced `DISCHARGE` only when `allow_export_discharge` is on** (deliberate grid export); when it's off (default) the EMS never force-discharges, so it can't dump power to the grid for free.
- **Command confirmation.** After a write, **poll HA/RPC state** for a few cycles and record whether the battery actually entered the desired mode. If not confirmed → it counts as a failure.
- **Failure behaviour.** On a failed/unconfirmed command: **retry once with backoff** → if still failing, command **`AUTO`** (safe) → raise the `battery_write_failed` alert (§9.3). Never leave the battery in an unknown forced state.
- **Manual-change tracking.** If the battery's mode changes **outside** the EMS (you flipped it in the app/HA, or it's in a vendor schedule), detect the divergence. Default policy: **respect a manual override** for `manual_override_respect_minutes` and surface it in the UI; after that, resume planning (configurable: `respect` vs `reassert`).
- **Don't fight vendor control.** In `ALLOW_SELF_CONSUMPTION` the EMS issues **no per-cycle commands** — the vendor's P1-zeroing controller owns instantaneous power. The EMS only acts on a *mode/intent change*; it never nudges live power to correct minor deviations (§2).
- **Restore original mode.** On graceful shutdown, "return to Indevolt default", or "pause until tomorrow", restore the **captured original vendor mode** (or plain `AUTO` if unknown) — manual EMS testing never leaves the battery in a surprise state (§13.3).
- **Rate limit (confirmed):** Indevolt recommends **≥ 5 s between requests (1 s min)**; the HA integration polls ~30 s. **Not** a continuous-modulation device. The EMS writes **only on a mode change** (target < 10 writes/day).
- **Continuity risk:** Indevolt is a young brand (since 2022). The HA/probe abstraction means swapping batteries later only touches `battery.py`.

---

## 7. Operating modes & control intent

The planner reasons in a high-level **`BatteryIntent`**; the mode controller maps **intent → physical mode → probe-resolved vendor action**. This three-layer split keeps the planner vendor-agnostic and is the seam where "don't fight vendor control" (§2) is enforced. Full detail + the worked mapping live in [`docs/control-model.md`](docs/control-model.md).

### 7.1 Control-intent layer (`BatteryIntent`) + compatibility matrix
The planner outputs *intent*, not raw commands. Each intent carries the data it needs (target SoC, deadline) and maps to a physical mode and a vendor action; the **"P1-zeroing active?" column is verified and stored at M1** (§6.5, §17), never assumed.

| `BatteryIntent` | Carries | Physical mode | Vendor action (probe-resolved) | P1-zeroing active? · CONFIRM@M1 |
|---|---|---|---|---|
| `ALLOW_SELF_CONSUMPTION` | — | `AUTO` | energy-mode select → self-consumption | **YES** — vendor controller runs; EMS does nothing per-cycle |
| `GRID_CHARGE_TO_TARGET` | `target_soc`, `deadline`, `power` | `CHARGE` | `indevolt.charge {power, target_soc}` | **NO** — forced charge |
| `HOLD_RESERVE` | `allow_solar_charge` | `IDLE` | standby button / floor = current SoC | **N/A / partial** |
| `DISCHARGE_FOR_LOAD` | `floor_soc`, `deadline` | `AUTO` (serve load) **/** `DISCHARGE` (export) | **`AUTO`/self-consumption if P1-zeroing serves load** (the normal case); **forced `indevolt.discharge` only when export is explicitly allowed** (`allow_export_discharge`) | **YES** (serving load) **/ NO** (forced export) |

- **`DISCHARGE_FOR_LOAD` is *not* a fixed-watt dump.** Serving the house during an expensive window is done by the vendor's self-consumption (`AUTO`) drawing down storage — *not* by the EMS tracking power every cycle (which §2 forbids). Force-discharge is reserved for deliberate grid export. See §8.3 step 4 and §6.5.
- **`HOLD_RESERVE.allow_solar_charge`** (config) decides whether holding reserve still lets *solar* charge the battery (summer "build toward sunset") or blocks all charge (pure freeze).
- **Narrow `car_session` exception (feat/car-charge-modes, §4.5).** `DISCHARGE_FOR_LOAD` also becomes a forced `DISCHARGE` when the caller passes `car_session=True` (`intent_to_mode`, `ems/sources/battery.py`) — **independent of `allow_export_discharge`** — for the two car-charging discharge behaviours (`static_discharge`/`match_home_load`). This is safe-enough *only* because the setpoint stays bounded to ~the predicted non-EV house load (not a fixed export dump) and the session is re-evaluated every control cycle; see §4.5 for the full rationale, the reserve floor, and the write-bound (dwell + cap + recommand) rules.
- **Preconditions (checked before any *overriding* intent — charge/discharge/hold):** battery online; control path enabled (probe ok); **grid charging allowed** (charge intents); **P1 linked to Indevolt** (paired-meter check, §6.5); **SoC valid** (plausible+fresh, §4.7); not inside the startup grace period (§13.4). If any fails → fall back to `ALLOW_SELF_CONSUMPTION` + alert.

### 7.2 Physical battery modes (what the controller actually commands)
| Physical mode | Battery behaviour | Driven by intent |
|---|---|---|
| `AUTO` | Self-consumption (vendor P1-zeroing controller runs) | `ALLOW_SELF_CONSUMPTION` |
| `CHARGE` | Force charge to a **target SoC** | `GRID_CHARGE_TO_TARGET` |
| `DISCHARGE` | Force discharge for **deliberate export only** (when `allow_export_discharge`) — serving load uses `AUTO`, not this | `DISCHARGE_FOR_LOAD` only when exporting |
| `IDLE` | Hold SoC (no charge/discharge) | `HOLD_RESERVE` |

> **IDLE validation & emulation (made precise).** A true hold requires either the **standby button** (if the probe confirms it holds SoC without dumping) or RPC `47015=0`. **If neither truly holds**, emulate IDLE by: (a) commanding `CHARGE` with `power≈0` / `target_soc = current SoC`, or (b) setting the **discharge floor (min SoC) to the current SoC** so `AUTO` cannot discharge below it, then `AUTO`. **Distinguish standby/hold from "self-consumption disabled"** if the battery exposes both — prefer the one that holds SoC without exporting. The probe (§6.5) records which is available; the chosen strategy is logged in the decision reason. **CONFIRM@M1.**

### 7.3 EMS strategy modes (the brain's high-level state)
| Strategy mode | Meaning | Default trigger |
|---|---|---|
| `SUMMER_SOLAR` | Fill battery from solar surplus; run the night on battery | Forecast daily solar ≥ threshold for N days, or month in Apr–Sep |
| `WINTER_ARBITRAGE` | Charge at price dip, discharge at price peak | Forecast solar low, or month in Oct–Mar |
| `MANUAL` | You pin a specific behaviour | Set by you in HA / web UI |

Selection is **configurable** (calendar month, rolling solar-forecast threshold, or manual). **Transition hysteresis** (§8.4) prevents the strategy flipping daily around the threshold.

---

## 8. The decision logic

> **The planner is a port, not a hard-coded algorithm.** Whatever produces the schedule must emit the same validated `Plan` (§8.11). A runtime, UI-editable **planner-mode switch** (`planner.mode`) selects the implementation:
> - **`rule_based`** (default) — the deterministic summer/winter logic in §8.2–§8.3.
> - **`ml`** — a learned planner (the optional ML layer) produces the executed `Plan` — and it passes the **unchanged §8.11 validator + all guardrails**; an invalid/`unsafe` ML plan falls back to `rule_based`, then `AUTO`, with an alert.
> - **`advisory`** — the ML planner runs *alongside*; its proposed plan + projected savings are **shown in the UI** for comparison, but the `rule_based` plan still executes (build trust before switching to `ml`).
>
> ML is optional and off on a plain Pi; full detail in [`docs/ml-layer.md`](docs/ml-layer.md). The rest of §8 specifies the **`rule_based`** planner. Energy-unit definitions (`usable_kwh`, the two distinct "reserve" quantities, where round-trip efficiency is consumed, the equivalent-cycle count) are pinned in [`docs/control-model.md`](docs/control-model.md) §4.

### 8.1 Inputs to a planning run
- Tomorrow + remaining-today **prices** in **15-minute slots** (Tibber `priceInfoRange QUARTER_HOURLY`; degrades to hourly via expansion, §6.2), validated complete.
- **Solar forecast** per slot (Solcast P10/P50/P90, or Forecast.Solar), with provenance + bounded correction (§6.3). **P50** expected; **P10** for commitments.
- **Expected `non_ev_load_w`** per slot — the app **learns this** as a rolling average per weekday+hour from HA Recorder (14-day window), built from the **reconstructed** load (§4), excluding EV (§4.5). Cold-start default (~500 W/h overnight) until enough history, then converges.
- **Current SoC** and battery limits (usable kWh, max charge/discharge power, min reserve SoC, **observed** values from the probe).
- **Usable energy now** and **remaining-day solar** (separate from the full-day forecast — see §8.9): the planner reasons in kWh and converts to a **target SoC** per charge window, not just "charge/idle".

> **The planner plans target SoC and deadlines, not just modes** (§8.9). Every charge window has a **target SoC derived from the required kWh** and a **deadline** by which that SoC must be reached. Math + worked example in [`docs/control-model.md`](docs/control-model.md) §4–§5.

### 8.2 Summer strategy — "run the full night on battery (+extra)"
**Objective:** **by sunset**, store enough to cover overnight load + reserve, using *solar surplus first*, buying grid only if the forecast says solar won't reach the target **before the sunset deadline**.

- **Sunset deadline:** the summer target SoC must be reached **by sunset** (`astral`), not by an arbitrary replan time. Schedule solar accumulation / any top-up to complete before sunset (§8.9).
- **Sunrise/sunset source:** computed with `astral` from the configured/pinned lat/lon; **fallback** if location/timezone is missing → use a fixed civil-twilight estimate for the configured timezone and **flag it**, rather than failing. **Implemented reality:** `ems/sky.py` computes sunrise/sunset from a self-contained NOAA solar-position equation, not the `astral` library — there is no `astral` dependency in `pyproject.toml` (also affects the `docs/energy-model.md`/`geo.py` framing in §13). Behaviourally equivalent for this purpose; correct if `astral`'s specific fallback semantics ever matter.
1. **Overnight need** = expected `non_ev_load_w` (sunset→sunrise) + `night_reserve_kwh`. Cap at usable capacity.
2. **Today's surplus** = forecast solar − expected daytime load. Use **P50** for the expected plan but **P10** when *guaranteeing* the overnight run (so a cloudy surprise doesn't leave you short).
3. **If surplus ≥ need:** daytime `AUTO` (solar fills the battery); evening/night `DISCHARGE`/`AUTO`. **No grid charging** — and specifically **do not grid-charge before a forecast strong-solar morning** (`avoid_precharge_before_solar`): a morning that will refill the battery for free makes pre-dawn grid charging wasteful.
4. **If surplus < need (cloudy):** schedule a **deficit-only** top-up `CHARGE` in the cheapest slots — **top up only the deficit, never the whole battery** (solar tops it up by day; don't waste cycles).
5. **Midday negative prices** are a **separate, explicit policy** — implemented as the opt-in **`planner.negative_price_soak`** bool (default **off**, so this is *dry-run before every live strategy*: today's behaviour is unchanged until you turn it on). When on, any slot priced **below €0** (you are *paid* to consume) becomes a battery-charge slot — up to headroom, even outside a normal cheap window and even when summer grid top-up is off. Each such slot carries the plain reason "price below €0 — you are paid to charge". (`allow_export` and `shift_ev` remain future policies; export *valuation* is handled separately by `prices.export_price_model`, §8.3.) Not bundled into normal self-consumption.

### 8.3 Winter strategy — "buy the dip, spend the peak" (with honest economics)
**Objective:** charge at the cheapest window(s), discharge at the most expensive, **serving load** (not dumping power), never running empty before the evening peak.

> **Implemented + validated (charging-algorithm research, [`docs/charging-algorithm-research.md`](docs/charging-algorithm-research.md)).** A backtest (`ems/sim.py`, four NL weather days, rolling replan) found the fixed night-carry target under-sized dull days *and* grid-charged overnight to chase the target even when morning sun would refill it for free (≈8 kWh wasted). The fix — **`ems/planner/adaptive.py`** (`plan_adaptive`) — sizes the battery to the *forecast* evening+overnight deficit, nets out conservative P10 solar, and grid-charges only the shortfall in the cheapest slots **before** the peak (so it shaves it). It is now the live **summer** engine. A DP cost-optimizer (`ems/planner/optimal.py`) confirms the heuristic is within **4% of the global optimum** (and keeps higher self-sufficiency); it stays as a yardstick / optional optimal mode. Result: 4-day grid cost €8.20 → €2.32 (−72%), never below reserve, safe under a 40%-rosy forecast.

1. Normalise prices to 15-min slots; rank them.
2. **Profitability test (replaces the fixed `arbitrage_min_spread_eur`).** A discharge slot is worth serving from stored energy only if:
   ```
   net_benefit_per_kwh = avoided_import_price
                       − (charge_price / round_trip_efficiency)
                       − degradation_cost_eur_per_kwh
                       − risk_margin_eur_per_kwh
                       (+ grid fee adjustments per §6.2)
   ```
   Trade only slots where `net_benefit_per_kwh > 0`. The old `arbitrage_min_spread_eur` remains as a coarse floor / sanity bound.
3. **Charge sizing → a target SoC by a morning deadline.** Compute the `required_kwh` to serve the profitable windows and convert it to a **`target_soc`** (§8.9); schedule `CHARGE` in the cheapest slots **before the first expensive period** (the morning-peak deadline). **Do not fill to 95% by default in winter** — charge to the computed `target_soc` (≤ the season ceiling), not a fixed ceiling.
4. **Discharge = serve load during expensive periods, *via the vendor's own self-consumption*** — not a per-cycle power-tracking loop (which §2 forbids). "Serve exactly the load" is achieved by letting the battery self-consume from storage (`DISCHARGE_FOR_LOAD` relies on P1-zeroing staying active in discharge — **CONFIRM@M1**, §6.5). The **force-discharge service is reserved for deliberate export** (`allow_export_discharge`). If the probe finds the vendor does *not* serve-load in forced discharge, "discharge during the peak" degrades to keeping the battery in self-consumption (`AUTO`) drawing down storage, never a fixed-watt grid dump.
5. **SoC reservation before the evening peak.** The morning-peak discharge floor is **computed, not a magic number**: `evening_reserve_kwh` = the energy the evening windows must serve (their `required_kwh`), so the projected SoC entering the evening peak ≥ `evening_reserve_kwh` above the reserve floor. Enforced via the **projected-SoC curve** (§8.5) and checked by the validator (§8.11). (Definition in [`docs/control-model.md`](docs/control-model.md) §4.)
6. **Cycle budget.** Respect `max_cycles_per_day` / `max_cycles_per_month` for arbitrage, where **one equivalent full cycle = (kWh charged + kWh discharged) / (2 × `usable_kwh`)** (definition in [`docs/control-model.md`](docs/control-model.md) §4); once exhausted, stop trading for the period.
7. **Hysteresis & no-trade mode.** On "barely profitable" days apply hysteresis (don't flip on a 1-cent wobble). If projected daily savings `< daily_min_savings_eur`, enter **no-trade mode** (`AUTO` all day) — the cycles aren't worth the wear.
8. Add forecast solar on top (reduces how much must be bought).

> **Implemented — post-2027 economics (`ems/planner/economics.py`).** Two shared, pure money functions back both live planners *and* the finance/savings math, so the arbitrage gate and the reported benefit never drift apart:
> - `breakeven(charge_price, …)` — the sell price a stored kWh must beat (charge price grossed up for round-trip losses + wear + risk margin). This is the profitability test in step 2, factored out.
> - `export_value(price, model, …)` — what one **exported** kWh is worth under a configurable feed-in model (`prices.export_price_model`):
>   - **`net_metering`** (default) — the full retail price (today's Dutch *saldering*: export nets against import at the full price);
>   - **`spot_minus_tax`** — spot price **−** energy tax (post-2027 dynamic export). This **may be negative** on a negative-spot slot — exporting can *cost* money — and is deliberately **not** clamped (§2 "negative prices & export tariffs are real");
>   - **`fixed`** — a flat feed-in tariff, independent of spot.
>
>   `day_finance` credits export via the selected model in **both** the actual and the no-battery baseline cost, so under a low feed-in the battery's measured benefit grows honestly rather than assuming export is free. The consume-side counterpart is the **negative-price soak** (`planner.negative_price_soak`, §8.2 step 5): charge when the price is below €0.

### 8.4 Strategy selection & seasonal hysteresis
- `summer_solar_threshold_kwh` is **roof-specific**, **calibrated from PVGIS / actual yield** (§6.3), not a guessed 12 kWh.
- **Transition hysteresis** (`strategy_switch_hysteresis_days`, `strategy_switch_band_kwh`): require the rolling solar forecast to stay above/below the threshold **by a band, for N consecutive days**, before switching strategy — so it doesn't flip daily near the boundary. Calendar months remain a coarse override.

> **Implemented (Loops 1–6).** Both strategies emit the same `Plan` (§8.6) and feed the same
> projection (§8.5):
> - `ems/planner/summer.py` — solar-first: fills the battery from PV, runs the night on it, and
>   grid-charges **only the shortfall** to the night-carry target in the cheapest slots before the
>   next sunset, within a price cap. Solar counted on for the guarantee is the **P10** (§6.3). The
>   **sunset deadline is derived from the solar forecast itself** (`sunset_after` — the last daylight
>   slot the panels actually see), which is preferred to a fixed clock time or an `astral` almanac
>   sunset (and keeps `astral` out of the Pi image — see `ems/sky.py`). Rolling PVGIS-calibrated
>   threshold remains a later refinement.
> - `ems/planner/rule_based.py` — winter price-arbitrage.
> - `ems/planner/strategy.py` — `select_strategy` (runtime mode `auto`|`summer`|`winter`; `auto`
>   decides by forecast surplus + price spread, §8.4 energy-condition pick) + `build_plan` dispatcher.
> - **Seasonal-transition hysteresis (B-15, implemented):** `resolve_strategy_hysteretic` +
>   `HysteresisState` in `ems/planner/strategy.py` damp the `auto` pick — a season change must hold
>   for `strategy.hysteresis_days` (default 3; 0 disables) consecutive daily evaluations before it
>   commits, so shoulder-month days can't flap summer↔winter. A single agreeing day resets the run;
>   the counter advances at most once per calendar day (so the 5-min loop can't fast-forward it).
>   A **fresh state commits the current pick immediately** (no first-evaluation delay — hysteresis
>   only damps *changes*). The counter is persisted in the KV cache (`strategy:hysteresis`), so it
>   is restart-safe, and threaded through `ems/replay.py` so replays match the live app. Forced
>   `summer`/`winter` bypasses hysteresis. `strategy_switch_band_kwh` remains a later refinement.
> - Runtime + UI: `strategy.mode` / `strategy.summer_grid_topup` / `strategy.summer_max_topup_price`
>   are editable; `GET /api/strategy` and the dashboard **strategy card** (segmented picker +
>   plain-language explainer + inline grid-top-up toggle) expose and tune it. Sizing reuses the
>   battery's overnight-load / reserve settings (§8.2 step 1).

### 8.5 SoC projection lives in the planner (not just the UI)
The planner computes a **projected-SoC curve** across the horizon as it builds the schedule, applying **charge/discharge efficiency** to each slot. The plan is **rejected/adjusted** if the projection would (a) drop below `min_reserve_soc`, (b) exceed usable capacity, or (c) fail to reserve enough for the evening peak (§8.3, step 5). The same curve feeds the UI's expected-vs-actual chart (§9.1).

> **Implemented (read/UI side):** `ems/planner/projection.py` simulates SoC + grid flow forward over the plan's slots from the current (cluster) SoC, the solar P50 forecast and a learned **non-EV** load profile (`ems/planner/load_profile.py`); it is served by **`GET /api/energy-forecast`** (recorded SoC history + 24h projection + summary) and rendered as the **SoC history-and-forecast chart**. Modelling choices + assumptions are documented in [`docs/energy-model.md`](docs/energy-model.md) §10.
>
> **Pre-apply projected-SoC gate (B-22, implemented):** the same projection now feeds the §8.11 validator. Rejects (a) any plan whose projection drops **below reserve** (`projection_below_reserve`, always on) and (b) a **grid-charge** plan whose projection **can't reach its own `target_soc` by its `deadline`** by a clear margin (>5 pp — `projection_short_of_target`, gated by `planner.validate_projection`, default on). Both are `unsafe` ⇒ fail safe to `AUTO`. The reachability gate is **conservative + data-quality-aware**: it runs only on `complete` inputs (a stale/absent forecast skips it — that's the data fail-safe's job, not this gate's), only on plans that *commit* to a grid-charge target (a summer solar plan's target is weather-hoped, sized by P10/top-up), and names the numbers in its reason ("targets 88% by 07:30 but projects 71%"). Reusing `projection.py` — not a second model — keeps the gate and the UI chart consistent.

### 8.6 The output: a mode schedule
```
00:00–02:00  IDLE       (holding 6.2 kWh bought yesterday; proj SoC 64%)
02:00–05:00  CHARGE     (cheapest window €0.08/kWh; charge ~2.5 kWh for tonight's peaks → proj SoC 64→90%, the winter ceiling)
05:00–07:00  IDLE
07:00–09:00  DISCHARGE  (morning peak €0.41/kWh; serve ~1.2 kW load; hold back enough for the evening peak → proj SoC 90→65%)
09:00–16:00  AUTO       (solar self-consumption)
16:00–17:00  IDLE
17:00–21:00  DISCHARGE  (evening peak €0.47/kWh → proj SoC 65→18%, above the 10% reserve)
21:00–24:00  AUTO
```
Each row carries a **human-readable reason** published to HA/UI. The control loop reads "now → mode" and acts only on transitions. **A "why not acting" reason** is produced when holding (e.g. "not discharging: spread €0.06 < net-benefit threshold after losses+degradation").

### 8.7 Worked examples (10.8 kWh battery, ~9.7 kWh usable, 3 kWp)
**Summer (sunny).** Solar P50 = 18 kWh; daytime load 6; overnight need 7 (incl. 2 reserve) < 9.7 ✓. Surplus 12 ≥ 7 → **no grid charging**, no pre-dawn charge (strong solar coming). Daytime `AUTO`; sunset→`DISCHARGE`/`AUTO`. Zero overnight import; reserve intact at sunrise.

**Summer (cloudy).** Solar P10 = 5 kWh; need 7 → deficit 2. Schedule a **2 kWh `CHARGE`** in the cheapest pre-dawn slots **only** (don't fill the battery), then run the night on battery. Buys exactly the shortfall.

**Winter (profitable).** Solar 3 kWh (negligible); dip €0.09 (02–05), peaks €0.42 (07–09) & €0.48 (17–21). Profitability test clears with margin. Size charge to **serve the two peaks' load** (not "fill to 95%"); reserve enough at 09:00 for the evening peak; `IDLE` between; `AUTO` overnight. ≈ €3/day gross, minus losses + degradation — only run because `net_benefit_per_kwh > 0` and projected savings ≥ `daily_min_savings_eur`.

**Winter (unprofitable).** Flat prices, spread €0.05; `net_benefit_per_kwh ≤ 0` → **no-trade mode**, `AUTO` all day. Reason published: "no arbitrage today: best net benefit −€0.02/kWh".

### 8.8 Safety & guardrails
- **Min reserve SoC** never discharged below (e.g. 10%).
- **Max writes/day** + **min dwell per mode** (§6.5).
- **Debounce / hysteresis** so threshold-hovering prices don't flap.
- **SoC-projection invariants** (§8.5): never below reserve, never exceed capacity, evening-peak reservation respected.
- **Stale-data failsafe:** missing/old forecast or prices, or stale P1 → fall back to `AUTO` (§4.6, §16).
- **Watchdog:** if the EMS dies, the battery remains in its last (safe) mode.

### 8.9 Target-SoC & deadline planning
The planner reasons in **kWh → target SoC → deadline**, not just modes ([`docs/control-model.md`](docs/control-model.md) §4–§5 has the math + worked examples):
- `usable_now_kwh = usable_kwh × (soc − reserve_soc) / 100`; `required_kwh = max(0, needed_kwh − usable_now_kwh)`; `target_soc = clamp(soc + required_kwh/usable_kwh × 100, reserve_soc, season_ceiling_soc)`.
- **Season target-SoC ceilings** (`target_soc_ceiling`, e.g. 90–95%): don't charge above the ceiling unless explicitly needed (cell longevity).
- **Deadlines:** summer target by **sunset**; winter target by the **first expensive period**. Charge windows are the cheapest slots *before* the deadline.
- **Remaining-day solar estimate** (now → deadline, separate from full-day forecast) decides whether grid charging is still needed; a sunny afternoon ahead can cancel a planned charge (`don't grid-charge if surplus arrives soon` / `charge only if target unreachable by solar before deadline`).
- **Partial charge is normal:** a window charges to `target_soc` and no more — it need not fill the battery.

### 8.10 Operating policies (config-driven knobs)
- **Solar-first vs price-first** (`borderline_day_policy`): on borderline days, wait for solar or buy cheap grid energy.
- **Comfort reserve vs economy reserve** (`reserve_policy`): bias toward waking up with more battery (comfort) vs. minimum cost (economy).
- **Minimum top-up** (`min_grid_charge_kwh`): never schedule tiny inefficient grid charges (e.g. < 0.5 kWh).
- **Maximum daily grid charge** (`max_daily_grid_charge_kwh`): hard cap so a bad forecast/config can't over-buy.

### 8.11 Plan as a validated, versioned object
The planner emits a **`Plan`** domain object (id/version, input snapshot, slots, projected SoC, confidence, data-quality badge, deadlines — §13.2). Before any plan is applied it passes the **plan validator**; low quality blocks acting:
- **Plan validator (pre-apply):** non-overlapping slots covering the horizon; every slot duration **≥ `min_mode_dwell_seconds`**; every `target_soc` within `[reserve, ceiling]`; each charge window **feasible** within its slots at `max_charge_w` **and** reaching target by deadline; projected SoC never < reserve / > capacity, evening peak reserved (≥ `evening_reserve_kwh`, §8.9); **no overriding action when data-quality = `unsafe`**; and the plan's remaining mode switches **fit the *remaining* same-day budget** (`max_mode_switches_per_day` − the persisted `switches_today`, §13.3) — not just the per-day total, so a mid-day replan can't schedule switches the runtime will then refuse. The validator runs identically whatever produced the plan (rule-based or `ml`). A failing plan → keep the prior plan or fail safe. **Projected-SoC gate (B-22, §8.5):** the "reaching target by deadline" and "projected SoC never < reserve" checks are now enforced from the plan's **forward projection** (`projection.py`, reused — not a second model): a grid-charge plan whose projection falls **>5 pp short of its `target_soc` by its `deadline`** is rejected `unsafe` (`planner.validate_projection`, default on; skipped when data-quality ≠ `complete`, so a stale forecast never triggers it). This is a *deliberate strengthening* of the shared validator — because the §8.11 gate is what an `ml` plan must also pass (an ML planner never bypasses it), improving the validator is exactly how the safety floor rises for both planners.
- **Plan confidence & data-quality badge** (`complete | degraded | forecast_only | price_fallback | unsafe`): from forecast age, price completeness, **per-signal** sensor freshness (§4.7), and forecast spread. Shown per plan in the UI (§9.1).
- **Invalidation → replan:** new prices, new forecast, **SoC deviation** beyond `soc_deviation_replan_pct` (planned vs. actual, §8.5), manual override, or a missed/failed command. A **`min_replan_interval_seconds`** caps replan frequency to avoid churn.

### 8.12 Charge completion & missed-window recovery
- **Charge completion:** if actual SoC reaches `target_soc` before the charge window ends, transition `GRID_CHARGE_TO_TARGET → HOLD_RESERVE` (or `ALLOW_SELF_CONSUMPTION`) for the remainder — don't keep forcing charge.
- **Missed-window recovery** (charge failed, or the Pi was down during the cheap window): on recovery decide **catch up / partial catch-up / skip** based on whether the still-available slots before the deadline clear the economics (winter) or whether solar will cover it (summer). Decision tree in [`docs/control-model.md`](docs/control-model.md) §10; the chosen path is logged on the `ActionDecision`.

> **Missed-window recovery (B-16, implemented):** `ems/planner/recovery.py` (pure). `check_charge_completion(plan, now, soc)` classifies a committed grid-charge plan's progress as on-pace / behind (tolerated) / missed / complete from the plan-level `target_soc` + `deadline`; **missed** = still short by more than a margin (default 5 pp) with the deadline still ahead and nothing left scheduled to charge before it (an outage, a run of held decisions, or a price spike that emptied the arbitrage pool). On a missed window `build_catch_up_plan` re-routes the charge to the **cheapest REMAINING slots before the deadline**, sized to the energy still short (reusing `charge_need.stored_kwh_per_slot` — the per-slot quantum, not duplicated), never overwriting a peak the plan is discharging into; too few hours left ⇒ an **honest partial** (target lowered to the achievable level so the plan stays self-consistent). Recovery is folded into `_current_plan` as a **pure, deterministic reshape**, so the recovered plan flows through the **unchanged §8.11 validator** (incl. the B-22 projection gate) and the control-layer caps/dwell — it **bypasses nothing** and only ever ADDS charging toward an already-validated target. It never re-selects the season, so the §8.4 hysteresis counter is untouched. The observable side (audit `plan recovered: …`, a calm B-37 notification on fire and on impossible-catch-up) runs once per cycle, **rate-limited to one recovery per window per day** via the KV cache. Behind `planner.recovery_enabled` (default **on** — the failure it fixes, waking up short before the morning peak, is the costly one; off = the prior behaviour, a missed window left as-is).

---

## 9. Configuration (single source of truth)

A single commented `config.yaml` (base defaults, mounted `:ro`). UI-editable values overlay it from a runtime settings store (§9 end). The **full key-by-key reference** — types, ranges, defaults, what each affects — is in [`docs/config-reference.md`](docs/config-reference.md) (kept *separate* from this sample so the sample stays readable).

```yaml
site:
  latitude: 52.1
  longitude: 5.1
  timezone: Europe/Amsterdam

battery:
  model: solidflex_2000    # Gen-2; 2-tower cluster, controlled as one device
  usable_kwh: 9.7          # ~90% of 10.8 kWh; CONFIRM@M1
  max_charge_w: 4000       # CONFIRM@M1 from HA power sensors / probe
  max_discharge_w: 4000    # idem; lower if a Gen-2 feed-in/output limit is set
  min_reserve_soc: 10      # %
  round_trip_efficiency: 0.90
  min_mode_dwell_seconds: 600        # backs up max-switches/day; anti-flap
  allow_export_discharge: false      # if false, serve load via AUTO; never force-discharge to export (§7.1/§8.3)
  manual_override_policy: respect    # respect | reassert
  manual_override_respect_minutes: 120
  takeover_policy: stand_down        # stand_down | override  (if battery already in a vendor schedule)
  startup_grace_seconds: 120         # observe-only after boot until HA entities settle (§13.4)
  soc_max_jump_pct_per_5min: 20      # plausibility: reject larger SoC jumps (§4.7)

solar:
  kwp: 3.0
  tilt: 35                 # CONFIRM for your roof
  azimuth: 0               # 0 = south; CONFIRM for your roof
  forecast_provider: solcast          # solcast (primary) | forecast_solar
  forecast_fallback: forecast_solar   # keyless, RATE-LIMITED (~12/hr) — not "uncapped"
  forecast_refresh_owner: ems         # ems | ha  (single owner of the Solcast budget)
  solcast_daily_call_budget: 10       # free Hobbyist (new account)
  solcast_refresh_times: ["07:00","09:00","11:00","13:00","15:00","17:00","19:00"]
  forecast_correction_bounds: [0.7, 1.3]   # clamp the rolling forecast/actual factor
  use_percentiles: { winter_commit: p10, summer_commit: p10, expected: p50 }

prices:
  provider: tibber                    # tibber | energyzero (free no-key fallback/cross-check)
  tibber_token: !secret tibber_token
  resolution: quarter_hourly          # quarter_hourly | hourly (auto-expands hourly→4×15min)
  cache_immutable_slots: true         # past slots never re-fetched (Tibber prices are immutable)
  tomorrow_required_by: "15:00"       # if tomorrow missing past this -> stale -> fallback
  grid_fees:
    tibber_total_includes_all: false  # CONFIRM@M0 for your tariff
    import_fee_eur_per_kwh: 0.0       # added on top if above is false
    export_fee_eur_per_kwh: 0.0
  export_price_model: net_metering    # net_metering (default, today's saldering) | spot_minus_tax (post-2027) | fixed
  energy_tax_eur_per_kwh: 0.13        # subtracted from spot when export = spot_minus_tax
  fixed_feed_in_eur_per_kwh: 0.01     # flat €/kWh paid per export when export = fixed
  export_tariff_eur_per_kwh: 0.0      # (legacy) flat export value; superseded by export_price_model

arbitrage:
  degradation_cost_eur_per_kwh: 0.05  # battery wear allowance per kWh cycled
  risk_margin_eur_per_kwh: 0.02
  arbitrage_min_spread_eur: 0.12      # coarse floor / sanity bound (NOT the only test)
  daily_min_savings_eur: 0.20         # below this projected saving -> no-trade mode
  max_cycles_per_day: 1.5             # equivalent full cycles for arbitrage
  max_cycles_per_month: 30
  min_grid_charge_kwh: 0.5            # never schedule tiny inefficient grid charges (§8.10)
  max_daily_grid_charge_kwh: 12       # hard cap on grid energy bought/day (§8.10)

consumption:
  source: learned          # learned (reconstructed load from HA) | fixed
  learning_window_days: 14
  cold_start_w: 500
  exclude_ev_from_baseline: true      # car meter subtracted ONLY while charging
  ev_charging_threshold_w: 200        # above this = "car is charging"

strategy:
  mode: auto               # auto | summer_solar | winter_arbitrage | manual
  summer_months: [4,5,6,7,8,9]
  summer_solar_threshold_kwh: 12      # CALIBRATE from PVGIS/actual yield (roof-specific)
  strategy_switch_hysteresis_days: 3  # consecutive days past the band before switching
  strategy_switch_band_kwh: 2.0
  night_reserve_kwh: 2.0
  avoid_precharge_before_solar: true
  negative_price_soak: false          # opt-in: charge when price < €0 (you're PAID to consume); off = today's behaviour (planner.*, §8.2 step 5)
  target_soc_ceiling: { summer: 95, winter: 90 } # don't charge above unless needed (§8.9; cell life)
  hold_reserve_blocks_solar_charge: false        # HOLD_RESERVE: false = solar may still charge (§7.1)
  borderline_day_policy: solar_first             # solar_first | price_first (§8.10)
  reserve_policy: economy                        # economy | comfort (§8.10)

control:
  cycle_seconds: 300
  max_mode_switches_per_day: 10
  replan_times: ["13:15", "06:00"]
  dry_run: true            # NEW control logic ships in dry-run first (§14)
  min_replan_interval_seconds: 600   # cap replan churn (§8.11)
  soc_deviation_replan_pct: 10       # planned-vs-actual SoC gap that triggers a replan (§8.11)

homeassistant:
  base_url: http://homeassistant.local:8123
  token: !secret ha_long_lived_token
  entity_map:              # explicit role->entity mapping (don't rely on discovery names)
    grid_power: sensor.p1_meter_active_power
    solar_power: sensor.solar_kwh_meter_active_power
    ev_power: sensor.car_kwh_meter_active_power
    battery_soc: sensor.indevolt_state_of_charge
    battery_power: sensor.indevolt_power
    # ... energy_mode select, standby button, discharge_limit number, grid_charge switch

mqtt:
  host: localhost
  topic_prefix: ems
  publish_discovery: true
  retain_config: true      # discovery configs retained; state retain per-entity (§9.2)

web:
  enabled: true
  bind: 0.0.0.0
  port: 8080
  auth: bearer             # bearer | basic  (LAN-only by default; never expose to internet)
  auth_token: !secret ems_web_token
  guest_readonly: true     # optional read-only dashboard without control
  # Frontend is React + Vite, built at image-build time and served by FastAPI (§9.1).
  # No runtime CDN: all deps (charts, Leaflet, fonts, icons) are bundled/self-hosted.
  theme: auto              # auto | light | dark

planner:
  mode: rule_based         # rule_based | ml | advisory  (UI-editable; §8). ml/advisory need the ML layer.

ml:                        # OPTIONAL forecaster/optimizer layer — off on a plain Pi; full schema in docs/ml-layer.md
  enabled: false           # master switch; auto-true when a supported accelerator is detected
  require_accelerator: true # load ML models only on CUDA (Jetson) | Metal/CoreML/MLX (Apple Silicon); else statistical baseline
  inference_timeout_seconds: 5
  load_forecast: { runtime: auto, model_path: /data/models/load_forecast.onnx, confidence_min: 0.6 }  # auto → onnxruntime(cuda|coreml) | torch(mps) | tensorrt
  optimizer:     { runtime: auto, model_path: /data/models/optimizer.onnx }
  training:      { schedule: "03:00", history_source: sqlite, min_history_days: 30 }

explainer:                 # how the "why" text is phrased — INDEPENDENT of the GPU/ML layer above
  mode: template           # template | local_llm | external_llm
  # template     = deterministic strings (default; offline; any device incl. Pi)
  # local_llm    = on-device LLM; needs an accelerator (Jetson CUDA / Apple Silicon Metal/MLX)
  # external_llm = cloud LLM API (e.g. MiniMax); works on a plain Pi; needs internet + a key (PRIVACY note §12)
  local:    { runtime: auto, model_path: /data/models/explainer.gguf, timeout_seconds: 8, max_tokens: 200 }  # llama_cpp/metal | ollama | mlx
  external:
    provider: minimax              # example; any OpenAI-compatible chat endpoint
    base_url: https://api.minimax.io/v1
    model: <model-id>
    api_key: !secret llm_api_key   # secret only; never logged/stored in SQLite (§12)
    timeout_seconds: 8
    max_tokens: 200
    share: reason_and_facts        # minimal redacted payload — the deterministic reason + the few numbers it cites; NEVER raw history/secrets

history:
  db_path: /data/ems.sqlite
  sample_seconds: 60
  retention_days: 365
  vacuum_on_start: true
  backup_keep: 7          # daily VACUUM INTO snapshots kept in <db_dir>/backups (0 = disabled)

health:
  ntp_check: true

dev:                       # local development / testing (e.g. on a Mac) — see §11.6
  mode: live               # live | mock | replay
  # mock  = fake Indevolt adapter + synthetic meters/prices/forecast (no HA/battery/GPU)
  # replay = feed saved sample API responses / an HA Recorder export from fixtures_dir
  # mock and replay FORCE dry_run=true and refuse all real writes
  fixtures_dir: /data/fixtures   # canned Tibber/Solcast/HomeWizard/HA payloads (§14)
```

> **Implemented reality — this sample is the target design, not today's `config.yaml`.** The shipped file is a minimal starter: `site.timezone`, `dev.mode`, `control.{dry_run,cycle_seconds,control_cycle_seconds}`, `history.{db_path,retention_days,backup_keep}`, `web.port`, `sources.mode`, `prices.provider`, `devices.{p1_ip,solar_ip,car_ip,indevolt_ip,indevolt_ips_extra,indevolt_port}`. Everything else above (battery/strategy/arbitrage/consumption tuning, site lat/lon/tilt/azimuth/kwp) lives **only** in the runtime settings store (`ems/settings.py`), edited through the UI, under a **different key layout** than sampled here — e.g. `battery.usable_kwh`/`.min_reserve_soc`/`.night_reserve_kwh`, `strategy.mode`/`.summer_grid_topup`, `planner.solar_confidence`/`.round_trip_efficiency`/`.degradation_eur_per_kwh`/`.risk_margin_eur_per_kwh`/`.negative_price_soak`, `site.lat`/`.lon`/`.kwp`/`.tilt`/`.azimuth`, `control.operational` (not `arbitrage.*`/`solar.*` as sampled above). The **`homeassistant:` and `mqtt:` blocks are entirely unbuilt** (§5.2, §9.2) — no such keys are read anywhere. The **`ml:` block is unbuilt** (M6 not reached — expected, not drift). Of `explainer.*`, `mode: external_llm` (MiniMax) **is implemented** (`explainer.api_key`/`.base_url`/`.model`/`.max_tokens`/`.timeout_seconds`/`.validate_hours`/`.cache_hours` runtime settings) but `local_llm` is not. A real, shipped settings group is **missing from this sample**: `notify.ntfy_url` / `notify.ntfy_topic` (BACKLOG B-20) configure the ntfy push channel (§9.3).

**Defaults vs. UI-editable settings.** `config.yaml` holds base defaults (`:ro`). UI-editable values — **location (map pin)**, tilt/azimuth, `night_reserve_kwh`, percentile choice, mode override (with expiry, §9.1) — live in a **runtime settings store** in `/data` (a `settings` table in the same SQLite DB). **Effective config = defaults + runtime settings.** **Secrets are never written to the settings store or logs** (§12).

---

## 9.1 Observability — web UI (primary)

Two surfaces: the EMS **web UI** (primary) and optional **HA entities** (§9.2). The UI serves from SQLite and survives an HA outage (read-only/stale — §5.2).

**Frontend stack & build (updated — supersedes the earlier vanilla/vendored plan).** A **React + Vite** single-page app, **built at image-build time** (a Node stage in a multi-stage Docker build) and served by FastAPI as static assets with an **SPA history-fallback** route; SQLite-backed data via the JSON/WS API below. **No runtime CDN — everything is bundled/self-hosted:** charts (npm dep, not a hand-vendored file), **Leaflet + its marker/CSS assets**, fonts (`@font-face`, self-hosted), and icons. The **one** allowed online resource is OSM map tiles, and only on `/setup`. Because the SPA shell is served by the EMS itself, "offline" means only the WAN/HA is down — the LAN dashboard still loads. **Quality bar (gate criteria for §6 visual tests / §7 done):** initial bundle **≤ 300 KB gzipped** (checked in CI), **WCAG 2.1 AA**, **light/dark theme** (`web.theme`), responsive down to phone width; **English-only in v1** but structured for i18n. Visual/UX testing uses **Playwright (e2e) + screenshot/visual-regression**, run headless on the build host (never on the Pi/Jetson).

**Setup vs. operations are split** (rec): two distinct screens.
- **Operational dashboard** (`/`) — the day-to-day view. **No Leaflet/map dependency** here; works with only the LAN up (HA may be down).
- **Setup/Settings** (`/setup`) — location map, provider/account fields, entity mapping, thresholds. This is the *only* screen that needs online map tiles.

**Top of the dashboard:** current **intent** + one-line reason + strategy + SoC, the **ownership state** (`observing` / `dry-run` / `controlling` / `manual-override` — §13.3), a **large, unmissable `DRY-RUN` / `LIVE` badge**, a **`FALLBACK ACTIVE`** badge when failsafe is engaged, and the **per-plan data-quality badge** (`complete | degraded | forecast-only | price-fallback | unsafe` — §8.11). **Per-signal freshness indicators** (each of prices / forecast / each meter / battery, §4.7) show green/amber/stale with last-update time.

| Graph | What it shows | Source |
|---|---|---|
| **Price curve** | Today+tomorrow 15-min, CHARGE/DISCHARGE/IDLE windows shaded; negative prices marked | Tibber (+ plan) |
| **Solar forecast vs actual** | P10–P90 band + P50 line + actual; provider/issue-time labelled | Solcast + solar meter |
| **Battery SoC** | Actual SoC + **projected-SoC curve**, with **expected-vs-actual divergence** highlighted | Indevolt + planner |
| **House load** | Learned baseline vs **reconstructed actual**; grid import/export; EV split out | derived (§4) |
| **Mode timeline** | Gantt strip of scheduled modes (next 24–36 h) **with per-slot reasons** | planner |
| **Savings** | Daily/cumulative arbitrage + self-consumption value | history |
| **Car card** | Car SoC (manual anchor + measured-charging estimate) with quick-set, weekly minimum-charge schedule, next deadline, and plug-in windows over the coming days on a price-coloured strip (v1: advisory only — §16) | car meter + `ems/ev_planner.py` |

> **Implemented reality — hero-first redesign (BACKLOG B-32, PR #20) supersedes the graph-table layout above as the *top* of the dashboard.** Before any graph, a **hero** answers the four consumer questions in order (what's happening / good-or-bad / what's next / do I need to act): a one-line verdict + a plain-language synthesis line + an explicit "Nothing needed from you." or a calm action line + a **plan-confidence chip** (BACKLOG B-68: high/medium/low, reason shown only when not high) + three **score pills** (self-consumption/CO₂/best-price, linking to Insights) + a header **notification bell** (BACKLOG B-20/B-59 — the `/api/notifications` outbox, unread count, §9.3). The graphs/table above remain available "one tap deeper," per B-32's done-when criterion; nothing in the table is removed, just no longer first.

**Explainability — "why is EMS not charging?" diagnostic panel:** beyond the active reason, a panel explains *inaction* concretely — which **precondition** failed (e.g. grid-charging disabled, P1 not paired), or the planner's logic ("holding: net benefit below threshold", "no-trade day", "target reachable by solar before sunset", "fallback: prices stale", "in startup grace"). The reason is always computed deterministically; the **`explainer`** (§9 config) only *phrases* it — `template` (default, offline), `local_llm` (accelerator), or `external_llm` (cloud, e.g. MiniMax — works on a Pi; privacy §12) — and any LLM phrasing falls back to the template string on failure, so the explanation is never blocked and never invents numbers.

**User controls** (all token-protected, same-origin/CSRF-checked):
- **Planner-mode switch:** `rule_based` ↔ `ml` ↔ `advisory` (§8, [`docs/ml-layer.md`](docs/ml-layer.md)). Disabled with an explanation when the ML layer isn't available (plain Pi). In `advisory` mode the dashboard shows the **ML plan vs. rule-based plan diff** + projected-savings delta so you can compare before switching to `ml`.
- **Return to Indevolt default** (emergency): restore the captured original vendor mode and set ownership to `observing` (§13.3).
- **Pause EMS until tomorrow:** stop commanding until local midnight (battery left in a safe restored mode).
- **Force next charge target** (one-off): override the next window's `target_soc` for a single correction.
- **Manual override with expiry:** "force AUTO for 6 h" / "force a mode until T" — written to runtime settings with an **expiration**; the UI shows the countdown and it lapses automatically.

**Export/download:** the **current plan** and **recent measurements** (CSV/JSON), an optional **weekly report** (plans, actions, savings, warnings — §16), and a one-click **export package** — a single ZIP of the whole history as CSVs (raw, derived, prices, **solar forecast vs. actual**, **plan history**, daily finance, **gas**, decision log) plus a `manifest.json` and a plain-language `validation_summary.txt`. The manifest carries a **redacted** config snapshot (allowlisted keys — **never tokens, IPs or location**), recorder health, an **incident rollup** (command failures / cluster mismatches / fallbacks / reverts), and a **Solar forecast skill** read (bias, MAE, p10–p90 band coverage of the day-ahead forecast vs. what actually happened — `ems/analysis.py`). Built for the operator's own analytics and to share a production health-check safely.

**Setup wizard (first run):** checks **P1 linked to Indevolt**, **battery reachable** (capability probe ok), **HA token valid**, **Tibber token valid**, **forecast valid** — each with a pass/fail and fix hint (mirrors the validation checklist at the top of this spec). Ends with a **first-run dry-run summary**: "Here's what I would have done today."

**Endpoints (FastAPI) — reconciled against the shipped app (BACKLOG B-26).** The unified control surface is `GET/POST /api/override` (force a mode / return-to-default / pause, with an optional expiry — audited) rather than the `POST /api/control/{…}` sub-routes originally sketched here; the export surface is `GET /api/export` (the ZIP package) + `GET /api/export/package` (its manifest summary) rather than `/api/export/{plan|measurements|weekly|package}`. **Not implemented:** `WS /ws` (the UI polls instead) and `GET /api/setup/checks` (no setup wizard yet — BACKLOG B-34, pool). Shipped routes, grouped by area:
- **Health:** `GET /health/live`, `GET /health/ready`.
- **Status / plan / control:** `GET /api/status`, `GET /api/plan`, `GET /api/plan-detail`, `POST /api/plan-preview`, `GET /api/battery-plan` (incl. plan confidence — BACKLOG B-68), `GET /api/battery`, `GET /api/strategy`, `GET /api/charge-need`, `GET/POST /api/override`, `GET /api/decision`, `GET /api/diagnostics` (why-not), `GET /api/freshness` (per-signal), `GET /api/alerts`, `GET /api/incidents` (control-health rollup), `GET /api/audit`, `GET /api/replay` (a redacted reproducibility bundle of the *current* plan/decision for offline debugging — not the historical-replay suite, which is the `make replay` CLI over `ems/replay.py`, BACKLOG B-77).
- **Forecasting / energy:** `GET /api/prices`, `GET /api/forecast`, `GET /api/energy-forecast`, `GET /api/energy-distribution` (a day's Sankey), `GET /api/energy-story`, `GET /api/sky`, `GET /api/series?metric=…`.
- **Insights & reporting (§9.1.1):** `GET /api/report?period=&date=`, `GET /api/finance`, `GET /api/savings` (legacy plan-based estimate — superseded on the dashboard by `/api/finance`, BACKLOG B-03b, but still served), `GET /api/accuracy` (BACKLOG B-72), `GET /api/advisor/solar-confidence`, `GET /api/counterfactual` (BACKLOG B-69), `POST /api/whatif` (BACKLOG B-73), `GET /api/digest` (the weekly Sunday read, BACKLOG B-58).
- **Notifications (BACKLOG B-20, §9.3):** `GET /api/notifications`, `POST /api/notifications/read`.
- **Car / EV advice (§16):** `GET /api/cars` (car-picker data), `GET /api/car/plan` (EV charging-advice plan), `POST /api/car/soc` (manual car-SoC anchor; token-protected + audited like `/api/override`), `GET /api/advisor/ev-charge`.
- **Settings / auth / AI / misc:** `GET/POST /api/settings` (token-protected, **same-origin/CSRF-checked**, §12), `GET /api/auth`, `GET /api/explainer`, `GET /api/ai/validation`, `POST /api/ai/validate`, `GET /api/faq`, `POST /api/chat`.
- **Export (§9.1, BACKLOG B-40):** `GET /api/export`, `GET /api/export/package`.

### 9.1.1 Insights & reporting

An **Insights** tab presents three self-explaining **0–100 scores (100 = best)** the operator can watch trend over time — **self-consumption** (share of produced solar used on-site; falls back to self-sufficiency with no sun), **CO₂** (% avoided vs. a no-solar/battery/EMS reference home; gas folds into the footprint automatically from the P1 gas meter (`total_gas_m3`, now ingested), honestly stepping the score down to flag heating as the biggest remaining cut), and **best-price** (grid-import volume-weighted price mapped onto the period's price range) — plus a **where-your-energy-went** panel: kWh **from** solar/grid/battery and **to** house/car (+ export, battery charge). Windows: **day / week / month / year**. Each score carries a plain-language reason (explainability §8.6). The daily **energy-distribution Sankey** attributes each 15-min slot **solar-first, home-before-car** (solar → home → car → battery → export; battery → home → car; grid covers the rest) and surfaces the **car-guard leak** — the `battery→car` band, which is ~0 when the guard works and a flagged diagnostic when it isn't. Read-only, rolled up from the SQLite history **off** the dashboard poll (no device load). CO₂ accounting factors are editable settings (`reporting.grid_co2_factor` ≈ 0.27 kg/kWh, `reporting.gas_co2_factor` ≈ 1.78 kg/m³). Modules: `ems/energy_flow.py` (allocation), `ems/scores.py` (pure scores), `ems/reporting.py` (assembly). Design: [`docs/superpowers/specs/2026-07-01-insights-reporting-design.md`](docs/superpowers/specs/2026-07-01-insights-reporting-design.md).

> **Implemented since — additional Insights surfaces (BACKLOG B-58/B-69/B-72/B-73, PRs #21/#24).** A **weekly digest** ("Your week") delivered in-app and via the notification outbox every Sunday 18:00 local (`ems/digest.py`, `GET /api/digest`, dedupe-guarded so it fires once); **forecast/plan accuracy** tracking — solar forecast skill, plan-execution error, load-baseline error (`ems/analysis.py`, `GET /api/accuracy`); a **counterfactual savings** comparison against no-battery and vendor-`AUTO` baselines (`GET /api/counterfactual`); and a read-only **what-if scenario** simulator — allowlisted A/B replay, clearly labelled "simulation — nothing is changed" (`POST /api/whatif`).

**Map/setup page:** **Leaflet** (vendored, no CDN) with **OSM tiles loaded only on the setup page**. Respect **OSM tile + Nominatim policies**: low-volume personal use, proper attribution, descriptive `User-Agent`, **no bulk/prefetch, and no autocomplete against public Nominatim** (geocode only on explicit submit). **Manual lat/lon entry** always works offline. The pin re-points the forecast, recomputes sunrise/sunset (`astral`), and triggers a replan; timezone optionally via `timezonefinder` (offline).

## 9.2 Home Assistant entities (optional, via MQTT discovery)

> **Planned — not yet implemented (BACKLOG B-18, pool).** There is no `ems/publish/` module and `paho-mqtt` is not a project dependency (`pyproject.toml`) — nothing below is wired up. The entities, the retained-topic policy, and the `POST /api/states` note describe the **target** design once HA integration (§5.2) is built, not current behaviour.

Published via **MQTT discovery** (`homeassistant/<component>/<object_id>/config`, each with `unique_id` + `device` so they persist across restarts):
- `sensor.ems_current_mode`, `sensor.ems_strategy`, `sensor.ems_reason`, `sensor.ems_plan` (schedule in attributes)
- `sensor.ems_forecast_solar_today_kwh`, `sensor.ems_overnight_need_kwh`, `sensor.ems_battery_soc`, `sensor.ems_projected_soc`
- `select.ems_mode_override`, `number.ems_night_reserve` — live controls written back to the EMS
- **Alert/binary_sensor entities (§9.3):** `binary_sensor.ems_prices_stale`, `…_forecast_stale`, `…_battery_write_failed`, `…_dry_run_active`, `…_fallback_active`

**Retained config/state policy:** discovery **config** topics are **retained** (so entities survive an HA/broker restart); **state** topics are retained for slow-changing values (mode, strategy, reason) and **non-retained** for fast telemetry to avoid stale reads on reconnect. **Control ownership:** if you change the mode override from the HA `select`, the EMS **persists it to the runtime settings store** (with expiry) so the two surfaces never disagree.

> States pushed via `POST /api/states` are transient and lost on restart — that's why we use MQTT discovery. Set `mqtt.publish_discovery: false` for web-UI-only.

## 9.3 Alerts
First-class alerts (UI badges + optional HA binary_sensors): **prices stale**, **forecast stale**, **battery write failed/unconfirmed**, **dry-run active**, **fallback active**, **meter missing/stale**, **NTP unsynced**, **Solcast budget exhausted**, **ML fallback active** (GPU/model/LLM unavailable or low-confidence → ran the baseline/rule-based path, §9.1/[`docs/ml-layer.md`](docs/ml-layer.md)).

> **Implemented (BACKLOG B-37, PR #20).** Every alert (`ems/alerts.py`) carries structured `safe` (is my home/battery safe?) and `action` (what can I do?) fields alongside `message`/`severity`, style-guarded by tests so no alert can describe a condition without a next step — "nothing needed, EMS handles this automatically" is treated as a complete, honest `action`, not a gap.
>
> **Implemented (BACKLOG B-20, PR #21) — a separate notification outbox**, distinct from the alerts above. `ems/notify.py`'s `Notifier.send()` stores sparse, dedupe-aware notifications (the weekly digest — §9.1.1 — forecast-driven detectors — BACKLOG B-75 — and backup-failure signals) and optionally pushes them to a phone via **ntfy.sh** or a self-hosted instance (`notify.ntfy_url`/`.ntfy_topic`, §9 config), surfaced via the dashboard's header bell (`GET /api/notifications`, `POST /api/notifications/read`). No APNs/native push and no required cloud dependency beyond the user's own ntfy target.

---

## 10. *(reserved — observability merged into §9)*

---

## 11. Deployment

> **Two targets, one codebase.** This section is the **Raspberry Pi** variant (single host: HA + Mosquitto + EMS together, CPU-only, no ML). The **Nvidia Jetson** variant — EMS + the GPU **ML layer** on the Jetson, with **HA running elsewhere on the LAN** — is specified in [`docs/jetson-deployment.md`](docs/jetson-deployment.md). The *same* lean EMS image runs on both; the ML forecaster/optimizer is **accelerator-gated** and loaded only when a supported accelerator is detected (CUDA on Jetson, Metal/CoreML/MLX on Apple Silicon — capability detection + `ml.enabled`), so the Pi image carries no GPU dependencies. (The **explainer** is separate — `template`/`external_llm` need no accelerator and work on the Pi; see §9 and [`docs/ml-layer.md`](docs/ml-layer.md).)
>
> **Implemented reality (BACKLOG B-26) — today's actual deployment is neither target below.** Production currently runs via `scripts/install.sh`: a **macOS install (Apple Silicon, e.g. Mac Mini)**, no Docker, no Home Assistant, no Mosquitto — a `uv`-managed venv + a `launchd` LaunchAgent (`com.jeroenniesen.ems`) that starts the EMS on login and restarts it if it crashes, reading/writing devices **directly** (§5.2) with `control.operational` **armed** (real, non-dry-run battery writes). §11.6 below currently undersells this: it frames macOS as *dev/mock/replay only, never a real battery* — true for `dev.mode: mock`/`replay`, but **`dev.mode: live` on this macOS install is the live production path today**, not a test harness. The Pi (§11.1) and Jetson (`docs/jetson-deployment.md`) targets, the HA/Mosquitto compose, and any systemd/Docker production path remain the intended **future** deployment (tracked as **BACKLOG B-53**, pool) — not yet built or run in anger.

### 11.1 Raspberry Pi (single host)

- **Hardware:** **Raspberry Pi 5, 8 GB**, booting from an **NVMe SSD via the official M.2 HAT+** (HA's writes kill SD cards). Pi 4 + USB-SSD also works.
- **Install method:** Only **HA OS** and **HA Container** are supported (Core-venv/Supervised deprecated). Because we run a **custom Python service**, the **default is HA Container on Raspberry Pi OS 64-bit + Docker Compose** — the EMS is a first-class service.
  - **Note:** HA **Container** has **no add-on system**, so **Mosquitto and any other services must be separate containers** (as in the compose below).
  - **Documented alternative:** if you want HA OS's managed **backups/add-ons**, run **HA OS on one host** and the **EMS as a container on a separate Pi/host** (or in the AppDaemon add-on, losing the standalone UI — §13). Pick one; the default is the single-host compose.
- **EMS container:** Python 3.12 (`asyncio`, `httpx`, `aiosqlite`, `pyyaml`, `paho-mqtt`, `astral`). Reads `config.yaml`, talks to HA + battery, publishes MQTT, serves the UI.
- **Networking gotcha:** HA needs `network_mode: host` (mDNS/USB/BT), so it's **not** on the compose bridge and **can't resolve other containers by name** — point HA at the broker via **host IP:1883**, and the EMS at HA via **host IP:8123**.

**Operational hardening (new):**
- **Health endpoints + Docker healthcheck:** `GET /health/live` (process up) and `/health/ready` (config loaded, HA reachable or explicit-degraded, DB writable). Wire `healthcheck:` in compose.
- **Graceful shutdown:** on SIGTERM, the **one and only** command issued is a **safe-mode restore** — set the battery to the **captured original vendor mode** (or `AUTO` if unknown) so it never stops mid-forced-charge/discharge (§13.3); best-effort confirm, finish the current DB write, then stop. No *new control* commands beyond that single restore.
- **NTP/time-sync health check** (`health.ntp_check`) — price/charge windows are time-critical; alert if the clock drifts.
- **Backups — implemented (B-52):** the daily maintenance loop takes an **online `VACUUM INTO` snapshot** of the live WAL DB to `<db_dir>/backups/ems-YYYYMMDD.sqlite` (skips if today's exists), rotating to the newest **`history.backup_keep`** (default 7; `0` disables). Failures log loudly but never kill the loop; last-backup status (time/size/ok/kept) surfaces at `GET /api/diagnostics` → `storage.backup`. Still back up `config.yaml` + a record of token *locations* (never values) yourself; restore procedure in the runbook. The migration-runner half of B-52 stays deferred until a schema change needs it.
- **Log rotation + DB maintenance:** rotating file logs (size/age capped); SQLite `retention_days` purge + periodic `VACUUM` (`vacuum_on_start`).
- **Resource limits:** set `mem_limit`/`cpus` (or `deploy.resources.limits`) on the EMS container so a runaway loop can't starve HA.
- **Reliability:** `restart: unless-stopped`; tune HA **Recorder** (`commit_interval`, `exclude`, `purge_keep_days`) or move to **MariaDB**; add **NUT** + graceful-shutdown if a UPS exists.

```yaml
# docker-compose.yml (sketch)
services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    network_mode: host           # required; use host IP for MQTT
    privileged: true
    volumes:
      - ./ha:/config
      - /etc/localtime:/etc/localtime:ro
      - /run/dbus:/run/dbus:ro
    restart: unless-stopped
  mosquitto:                     # separate container (HA Container has no add-ons)
    image: eclipse-mosquitto
    ports: [ "1883:1883" ]
    volumes: [ "./mosquitto:/mosquitto/config" ]
    restart: unless-stopped
  ems:
    build: ./ems
    ports: [ "8080:8080" ]
    environment:
      HA_URL: http://<host-ip>:8123
      MQTT_HOST: <host-ip>
    volumes:
      - ./ems/config.yaml:/app/config.yaml:ro
      - ./ems/data:/data                 # SQLite + backups (persist)
    mem_limit: 512m
    cpus: 1.0
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health/ready').status==200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
    stop_grace_period: 30s
    depends_on: [ homeassistant, mosquitto ]
    restart: unless-stopped
```

### 11.5 HA startup validation
On boot the EMS validates HA: required **mapped entities** (`entity_map`) exist; `state_class`/`device_class`/**units** are sane (W vs kW, kWh); the `indevolt.*` capability probe (§6.5) succeeded. A failed mandatory check → start in **degraded/`AUTO`** mode and raise an alert, never silently mis-read.

### 11.6 Local development & testing (macOS or any host)
You can run the whole app on a laptop (e.g. a **Mac**) with **no Home Assistant, no battery, and no GPU** — for development and visual testing. The EMS is pure Python 3.12 + FastAPI serving the built React/Vite SPA, so it runs on any OS/arch (Apple Silicon arm64 or Intel amd64).

- **Dev/mock mode** (`dev.mode: mock` or `replay`, §9): swaps every live source for the **fake Indevolt adapter** (`CapabilityReport`-driven, §14), synthetic or **replayed** prices/forecast/meters (the saved sample responses / an HA Recorder export in `fixtures_dir`). It **forces `dry_run=true`** and refuses all real writes — so it can never touch hardware. The real UI runs against this simulated backend, which is exactly what the **Playwright + visual-regression** suite (§14, the §6 visual gate) drives.
- **Run it on a Mac via Docker:** a **`docker-compose.dev.yml`** builds the lean EMS image (multi-arch; no GPU/ML deps) and runs it with `dev.mode: mock`; dashboard at `http://localhost:8080`, SQLite in a local volume. No `network_mode: host`, no HA, no broker required.
- **Fast UI iteration:** run the **Vite dev server** (`npm run dev`, HMR) proxying `/api` + `/ws` to the Python backend — edit the React UI without rebuilding the image.
- **ML on a Mac (first-class):** Apple Silicon runs the ML layer **natively** via **Metal / Core ML / MLX** (and the local LLM via Ollama or llama.cpp-Metal) — capability detection picks the Metal/CoreML backend just as it picks CUDA on a Jetson. **Caveat:** Docker Desktop on macOS can't pass the GPU/ANE into a container, so on a Mac the **ML sidecar runs natively** and the EMS talks to it over `localhost` (same sidecar pattern as the Jetson). Or skip local ML entirely and use the **`external_llm` explainer** for smarter text. (You can still `mock` the ML adapters for hermetic tests.)
- **Scope:** `dev.mode: mock`/`replay` are for **testing only** — they force `dry_run=true` and never control a real battery. **`dev.mode: live` is not test-only:** per the reality note at the top of §11, it is today's actual production path on macOS, with real, armed battery writes. The Pi (§11.1) / Jetson ([`docs/jetson-deployment.md`](docs/jetson-deployment.md)) targets remain the planned future deployment (BACKLOG B-53).

---

## 12. Security

- **Auth:** a **bearer token over LAN**. Writes (override / settings / control) **always** require the token when one is set; reads are open on the LAN by default (guest read-only) but can be locked with **`web.require_auth`**, which requires the token for **every `/api/*` read too** — turn it on before reaching the app over a VPN or from outside the home. Auth is one pure-ASGI choke point in front of the whole JSON API (no per-endpoint guards; no forwarded/proxy headers trusted). **Do not expose the EMS port to the internet.** **Remote access = the LAN over a VPN** (never a public proxy) — see [`docs/remote-access.md`](docs/remote-access.md) for the supported model, trust boundaries, token rotation, logging and control permissions.
- **Secrets** (Tibber/Solcast/HA/web tokens, **external-LLM API key**) via **env / secret files only** — **never** in the SQLite settings store, never in logs.
- **Redaction:** tokens/keys are **redacted from any debug dump, export, or log line**.
- **CSRF / same-origin:** settings `POST`s require a same-origin check or CSRF token (the UI is browser-accessible).
- **Guest mode (optional):** `web.guest_readonly` serves a **read-only dashboard** (no control, no settings) for sharing.
- **External-LLM explainer privacy (`explainer.mode: external_llm`).** This is the **one feature that sends data off the device** — a deliberate, bounded exception to the local-first principle ([`GOAL.md`](GOAL.md) §3). It is **off by default and opt-in**; it sends only the **minimal redacted payload** (`share: reason_and_facts` — the already-computed deterministic reason plus the few numbers it cites), **never** raw history, tokens, location, or secrets; it never affects control; and on any failure it falls back to the offline `template` explainer. Document the chosen provider's data-handling for the user.

---

## 13. EMS Core — internal structure

```
ems/
  main.py            # FastAPI app + asyncio loop: sense → plan? → validate → decide → act → confirm → record → publish
  config.py          # load & validate config.yaml + entity_map; overlay runtime settings
  domain.py          # BatteryIntent, Plan, CapabilityReport, PlannerInputSnapshot, ActionDecision (§13.2)
  ports.py           # interfaces: LoadForecaster, Planner, Explainer, SolarForecaster, PriceSource, BatteryDriver
  lifecycle.py       # ownership state machine, boot sequence, startup grace, persistent counters/warnings (§13.3–§13.4)
  capabilities.py    # detect best accelerator (CUDA / Apple Metal-CoreML-MLX / CPU) → pick ML backend & gate the layer; §11/docs/jetson-deployment.md
  timeutil.py        # tz-aware 15-min slot utilities — naive datetimes never enter the planner (§13.1)
  sources/
    homewizard.py    # raw meter reads (via HA or direct local API); per-signal freshness + plausibility (§4.7)
    load_model.py    # reconstruct house_load / non_ev_load (§4); sign normalisation; calibration
    tibber.py        # prices: priceInfoRange (under currentSubscription) + hourly; cache; expansion; completeness
    solar_forecast.py# Solcast (primary, P10/P50/P90) + Forecast.Solar fallback; budget ledger; bounded correction; provenance; remaining-day estimate
    battery.py       # Indevolt cluster: capability probe→CapabilityReport, intent→mode→vendor mapping, idempotency, confirm, recover, restore-original (ONLY writer)
    ha.py            # HA WS/REST client + startup validation
  planner/
    base.py          # RuleBasedPlanner implements the Planner port; planner-mode switch selects the impl (§8)
    intent.py        # BatteryIntent selection + target-SoC + deadlines (§8.9)
    summer.py        # §8.2
    winter.py        # §8.3 (economics, cycle budget, reservation, no-trade)
    schedule.py      # Plan structure + "intent at t" + projected-SoC + "why not" reasons
    validate.py      # plan validator + confidence/data-quality badge (§8.11) — runs on EVERY plan, rule-based or ml
    recovery.py      # charge-completion + missed-window recovery (§8.12)
    explain.py       # TemplateExplainer (default, offline) + ExternalLlmExplainer (cloud API, e.g. MiniMax — HTTP only, NOT gpu-gated) implement the Explainer port (§8.6)
  ml/                # OPTIONAL — loaded only when capabilities + ml.enabled allow; not packaged in the Pi image (docs/ml-layer.md)
    load_forecaster.py # ML LoadForecaster adapter (else statistical baseline in load_model.py)
    optimizer.py     # MlPlanner adapter — emits the SAME Plan; passes the UNCHANGED validator
    explainer.py     # LocalLlmExplainer adapter (on-device LLM via the accelerator; rephrases the deterministic reason, no invented numbers)
    training.py      # on-device nightly retrain on the SQLite history (§4.3)
  control/
    mode_controller.py # intent→action, dwell, max-switches/day (persisted), idempotency, failsafe, dry-run gate, apply+confirm
  storage/
    history.py       # SQLite: raw vs derived tables; retention/vacuum (aiosqlite)
    settings.py      # runtime settings (location/overrides w/ expiry, planner.mode) + effective-config overlay
    runtime_state.py # persisted plan id/version, last-action req/confirmed, daily switch counter, unresolved warnings (§13.3)
  geo.py             # sunrise/sunset (astral) + fallback; optional lat/lon→tz (timezonefinder)
  web/
    api.py           # JSON + WS: status, plan, series, settings, freshness, export, health, controls (incl. planner-mode switch)
    frontend/        # React + Vite SOURCE (built at image-build time)
    static/dist/     # built SPA bundle served by FastAPI (SPA fallback); all deps bundled, no CDN (§9.1)
  publish/
    mqtt.py          # MQTT discovery + state + alert entities (retain policy §9.2)
  alerts.py          # alert state machine (§9.3)
  tests/             # planner/economics/load-model/battery-mapping/DST/property/scenario + Playwright UI/visual + ML-port-contract tests; fake Indevolt adapter (§14)
```

> **Implemented reality (BACKLOG B-26) — the actual `ems/` layout has grown well past the tree above; reconciling file-for-file.** Present and matching in spirit: `main.py`, `domain.py`, `sources/battery.py`, `sources/tibber.py`, `control/mode_controller.py`, `storage/history.py`, `storage/settings.py`, `web/api.py`, `alerts.py`. **`config.py`** is deliberately minimal, not the full §9 schema (see the callout in §9). **Renamed/relocated vs. the sketch above:** `geo.py` → **`sky.py`** (self-contained NOAA sunrise/sunset, not `astral` — §8.2); `sources/homewizard.py` → **`sources/live.py`**; `sources/solar_forecast.py` → **`sources/forecast.py` + `sources/forecast_solar.py`** (Forecast.Solar only — §6.3); `planner/base.py` → **`planner/rule_based.py`** + `planner/summer.py` + `planner/adaptive.py`; `planner/validate.py` → **`planner/validator.py`**. **Do not exist:** `sources/ha.py`, `ports.py`, `publish/mqtt.py`, `ml/*`, `storage/runtime_state.py` (HA/MQTT/ML unbuilt — §5.2/§9.2; `ports.py` is intentionally **kept** here as the target seam for **BACKLOG B-47**, not deleted). **New — shipped since this tree was written (BACKLOG B-26, PRs #13–#24), not listed above:** `analysis.py` (forecast/plan accuracy, B-72), `cars.py` + `ev_planner.py` + `ev_schedule.py` + `ev_session.py` + `ev_advisor.py` (EV charging advice, §16, B-17), `confidence.py` (plan confidence, B-68), `connection.py` (live device wiring), `detectors.py` (forecast-driven notifications, B-75), `diagnostics.py`, `digest.py` (weekly digest, B-58), `export_package.py` (support bundle, B-40), `finance.py` + `savings.py` (measured savings, B-03a/b), `freshness.py`, `intelligence/planning.py` (probabilistic planning scenarios — first slice of B-63/B-64/B-65), `notify.py` (notification outbox, B-20), `readiness.py`, `reporting.py` + `scores.py` + `energy_flow.py` (Insights, §9.1.1), `replay.py` (the historical-replay suite CLI, B-77 — distinct from the `/api/replay` endpoint, §9.1), `retrospect.py`, `sense.py`, `settings.py` (the runtime settings store), `sim.py`, `sky.py`, `weather.py`; `web/context.py` (`AppContext`, injected into routers) + `web/routes/{accuracy,car,digest,export,notify,whatif}.py` (the B-25 router-split first slice, `api.py` 3618→3172 lines); `planner/charge_need.py` + `planner/economics.py` + `planner/optimal.py`; `control/failsafe.py` + `control/loop.py` + `control/override.py` (`loop.py`'s `ControlLoop` exists but is **not yet wired into `create_app`** — BACKLOG B-46, the control brain still lives as closures); `storage/audit.py` + `storage/cache.py` + `storage/control_state.py`.

**Control loop (pseudocode):**
```python
async def cycle():
    raw   = await read_raw_sources()           # meters, SoC, price-now; per-SIGNAL freshness + plausibility (§4.7)
    state = reconstruct(raw)                    # §4: house_load, non_ev_load (sign-normalised)
    if ownership in (INACTIVE, GRACE): return   # boot/grace: observe only, no commands (§13.4)
    if plan_invalidated(state) and replan_allowed():    # §8.11 reasons; min_replan_interval
        prices = await tibber.prices_normalised()        # cached+forward; quarter→expand; validated complete
        if not prices.complete_for_planning(): return failsafe("prices incomplete")  # stay AUTO
        plan = build_plan(prices=prices, solar=await forecast.solar(),    # P10/P50/P90 + provenance + remaining-day
                          load=history.baseline(), soc=state.soc, cfg=config)  # intent + target_soc + deadlines + projected SoC
        if not validate(plan): return failsafe("plan invalid")            # §8.11 validator
        persist_and_publish(plan)                                         # plan id/version (§13.2)
    intent  = plan.intent_at(now())             # BatteryIntent, not a raw command (§7.1)
    if plan.data_quality == "unsafe" or not data_is_fresh(state): return failsafe("unsafe/stale")  # → AUTO
    intent  = recovery.adjust(intent, state)    # charge-completion / missed-window (§8.12)
    if respecting_manual_override(): return                               # §6.5
    if not preconditions_ok(intent): return failsafe("preconditions")     # §7.1
    desired = map_to_mode(intent, capability_report)                      # §6.5
    if desired != state.battery_mode and dwell_ok() and switches_today < cfg.max_switches:
        if dry_run: log_decision(intent, desired); return                 # §14
        ok = await battery.apply_and_confirm(desired)                     # §6.5 confirm
        if not ok: await battery.recover()                                # retry→AUTO→alert
        switches_today += 1                                               # persisted, keyed by local date (§13.3)
    publish_status(state, intent, desired)      # intent + reason (+ "why not")
```
The loop runs every `cycle_seconds` but **writes only on a confirmed mode/intent transition**, honouring the Indevolt ≥5 s / coarse-setpoint constraint with a wide margin. In `ALLOW_SELF_CONSUMPTION` it issues **no command** — the vendor controller owns live power (§2).

### 13.1 Timezone-aware slots
All 15-min slot math goes through `timeutil.py`; **naive datetimes never enter planner code** (DST correctness — §14 DST tests).

### 13.2 Domain objects (define early, before UI work)
`BatteryIntent` (enum, §7.1), `CapabilityReport` (probe output, §6.5), `Plan` (id/version, input snapshot, slots, projected SoC, confidence, data-quality, deadlines), `PlannerInputSnapshot` (saved **with every plan** for audit/replay), and `ActionDecision` (intent · command · reason · preconditions · outcome). Every `ActionDecision` references the **plan id/version** that produced it, so any action is traceable to its plan. Full field lists in [`docs/control-model.md`](docs/control-model.md) §9.

### 13.3 Runtime state & ownership
- **Ownership state machine:** `INACTIVE → OBSERVING → (DRY_RUN | CONTROLLING)`, with `MANUAL_OVERRIDE` as an overlay (diagram in [`docs/control-model.md`](docs/control-model.md) §7). The UI shows the current state (§9.1).
- **Boot sequence:** observe first → validate sensors (§11.5) + run capability probe (§6.5) → load/restore the last plan → **capture the battery's original vendor mode** → only then consider acting.
- **Restore original mode** on graceful shutdown / "return to default" / "pause" (§6.5, §9.1).
- **Persisted across restarts:** plan id/version, `last_action_requested`/`last_action_confirmed`, the **per-day switch counter (keyed by local date)**, and **unresolved warnings**.

### 13.4 Startup grace period
After boot/restart the EMS stays in `OBSERVING` for `startup_grace_seconds`, issuing **no** battery commands, so it doesn't act on half-populated HA state while entities settle.

**Runtime alternative:** `planner/` + `control/` could run inside **AppDaemon** (kept as fallback), but you'd lose the self-contained web UI/SQLite history — so the standalone service is recommended.

---

## 14. Testing & validation

Planners are unit-testable with canned prices/forecasts and a mocked battery — **no hardware in tests**.

- **Planner scenarios:** sunny summer, cloudy summer, **profitable winter, unprofitable winter (no-trade)**, **negative prices**, **stale data** failsafe.
- **DST tests (Europe/Amsterdam):** spring-forward (92 quarter-hours) and fall-back (100) days for price-slot alignment and schedule timing.
- **SoC-projection tests:** charge/discharge efficiency applied; invariants (never below reserve, never exceed capacity, evening-peak reservation).
- **Battery command-mapping tests:** mocked HA services/entities **and** mocked RPC; verify probe-driven mapping, idempotency (no resend when already in mode), confirmation, and failure→AUTO recovery. Use the **fake Indevolt adapter** (`CapabilityReport`-driven, no HA/hardware) so restart/recovery tests run deterministically.
- **Target-SoC & feasibility tests:** the §8.9 formulas (`usable_now`, `required_kwh`, `target_soc` clamping to ceiling) and charge-window feasibility (energy reachable within slots before the deadline).
- **Restart & recovery tests:** restart **during an active charge window** (resume correctly from persisted state); **actual SoC below planned → replan** (§8.11 deviation); **missed charge window → catch-up/partial/skip** (§8.12).
- **Scenario runner + golden fixtures:** a small runner that takes `{soc, prices, forecast, expected_load}` and prints the `Plan`; golden fixtures for `cloudy_summer_topup`, `sunny_no_topup`, `cheap_night_before_expensive_morning`, `missed_charge_window`.
- **Integration contract tests:** saved sample API responses (Tibber quarter-hour + hourly, Solcast, Forecast.Solar, HomeWizard v1/v2, HA states) — provider abstraction tested against canned payloads.
- **Property/invariant tests:** never below reserve, never exceed capacity, **never exceed max writes/day**, no overlapping/contradictory modes, dwell respected, `target_soc ≤ ceiling`.
- **Plan-validator tests:** the §8.11 rejection rules (overlap, infeasible window, out-of-bounds target, unsafe data quality, **sub-dwell slot**, **remaining same-day switch budget**).
- **UI / visual tests (Playwright):** e2e flows + **screenshot/visual-regression** run headless on the build host; assert the bundle-size budget, WCAG 2.1 AA checks, light/dark themes, and the **explainability check** (the "why-not" panel and every metric carry an explanation). These are the §6 visual-experience gate.
- **ML port-contract tests (when the ML layer is built):** the statistical baseline and the ML adapter satisfy the **same** `LoadForecaster`/`Planner`/`Explainer` interface; an `MlPlanner` plan passes the **unchanged** §8.11 validator; degradation triggers (GPU/model absent, timeout, low confidence, invalid plan) **fall back** to baseline/rule-based; `advisory` mode executes the rule-based plan while surfacing the ML diff. (See [`docs/ml-layer.md`](docs/ml-layer.md).)
- **Backtest/simulation mode:** replay **historical HA Recorder data** to compare the plan against what actually happened (uses the reconstructed load, §4).
- **Calibration + dry-run acceptance gate (milestone gate, not optional):** for **every new control strategy**, run a **dry-run acceptance period** (several days: log decisions, no writes) and compare *plan vs. actual* before enabling writes. This is the gate between M-read and M-control, and between each strategy milestone (§15).

---

## 15. Build plan (milestones)

Split for tighter, independently-shippable steps; **ingestion before UI**, **read/probe before write**, **dry-run before every live strategy**. The UI is **React + Vite** throughout (§9.1); the **ML layer is a late, optional milestone** gated on the Jetson.

- **M0a — Ingest + store + scaffolding.** Config + `entity_map`, HA read client + startup validation, **load reconstruction (§4)** with per-signal freshness + plausibility, SQLite raw/derived store, **domain objects + `ports.py` interfaces + tz-slot utilities + the ownership state machine & startup grace (§13) defined up front**, `/api/status`. *(read-only)*
- **M0b — Dashboard + setup.** **React + Vite** operational dashboard (LAN-only-capable) + setup page with map; Playwright/visual-test harness stood up; freshness indicators; dry-run/live badge.
- **M0c — Prices & forecasts normalised.** Tibber (cache, quarter↔hourly, completeness) + Solcast/Forecast.Solar (budget ledger, provenance, bounded correction) → 15-min slots; first graphs.
- **M1a — Battery read-only capability probe.** Build the `CapabilityReport`: Indevolt services/entities, energy-mode options, standby vs self-consumption-off, power min/max, discharge floor, grid-charging switch, **P1 pairing**, **P1-zeroing-by-mode** (verify + store), and **capture the original vendor mode**. **No writes.**
- **M1b — Battery writes.** Implement read SoC + the `BatteryIntent`→mode→action mapping via the probed surface; target-SoC charge; idempotency + confirmation + failure→AUTO + restore-original; verify a manual switch.
- **M2 — Winter arbitrage.** Economics test, **target-SoC + morning-deadline** charge sizing, IDLE hold, projected SoC + evening reservation, no-trade mode, **plan validator + charge-completion + missed-window recovery**; overlay plan on graphs; **dry-run acceptance period → then enable**.
- **M3 — Summer solar.** Overnight-need + **sunset-deadline** + deficit-only top-up; remaining-day solar guard; auto strategy switch w/ hysteresis; projected-SoC curve; **dry-run → enable**.
- **M4 — Polish.** Guardrails, failsafe, max-switch/dwell caps, reasons + "why not" diagnostic panel, user controls (return-default/pause/force-target), data-quality badge, savings graph, web-UI auth/CSRF, alerts, MQTT entities, backups, the **3 global visual-polish passes** (§6 of `GOAL.md`).
- **M6 — Optional ML layer (accelerator-gated).** Behind the `ports.py` interfaces: ML `LoadForecaster`, `MlPlanner`, and the `local_llm`/`external_llm` explainers; on-device training; the **planner-mode switch** in the UI. Runs on any supported accelerator (CUDA on Jetson, Metal/CoreML/MLX on Apple Silicon). Ship **`advisory` first** (compare ML vs rule-based in the UI), then enable **`ml`** only after a dry-run acceptance comparison; the `external_llm` explainer is independent and works on a plain Pi. Never bypasses the §8.11 validator. Full spec: [`docs/ml-layer.md`](docs/ml-layer.md); deploy: [`docs/jetson-deployment.md`](docs/jetson-deployment.md).
- **EV control — *separate v2 spec*** (`docs/v2-ev-control.md`), **not** a milestone here (§6.4, §16).

Each milestone is independently useful and testable.

---

## 16. Decisions, defaults & scope notes

**Resolved by research (verified per source — see §6):**
- **Indevolt** — official HA integration provides **`indevolt.charge`/`indevolt.discharge` services only**; standby/energy-mode/discharge-floor/grid-charging are **entities** (button/select/number/switch), not services → **probe at M1a**, map accordingly, RPC fallback.
- **Tibber** — `priceInfoRange` is **under `currentSubscription`** (not top-level), `resolution`-arg, capped; `today`/`tomorrow` are hourly. Cache immutable past slots; expand hourly→4×15min. EnergyZero/ENTSO-E = fallback/cross-check.
- **Solar forecast** — Solcast Hobbyist primary (P10/P50/P90), Forecast.Solar keyless **rate-limited** fallback (~12/hr, not uncapped); Open-Meteo optional only; PVGIS once for the threshold baseline. EMS owns the refresh budget. **Implemented reality: Solcast is not yet built (BACKLOG B-14, pool) — Forecast.Solar is the only shipped provider (§6.3).**
- **EV** — v1 read-only via HomeWizard car meter; control is a **separate v2 spec**.

**Defaults taken (override in `config.yaml`):** read telemetry **directly from devices** (HA integration not yet built — §5.2); write via **direct OpenData RPC** (not yet via the probed HA Indevolt surface — §6.5); 15-min planner slots; replan after 13:00 prices + dawn; strategy auto-switches by month **and** a rolling solar threshold **with hysteresis**; **UI = React + Vite, served by the EMS, no runtime CDN**; **planner mode = `rule_based`** (ML off); always overridable.

**Hardware confirmed:** SolidFlex 2000 Gen-2, 2-tower cluster, latest firmware (control as one device; Gen-2 power/feed-in/grid-charge entities). Overnight load ≈ 500 W used only as cold-start; the app learns the real baseline.

**EV charging advice (v1: visual only).** The Car card (§9.1) tells the user *when to plug in* to meet a per-day-of-week minimum-SoC schedule (`enabled`/`min_pct`/`ready_by`, default 07:30) as cheaply as possible — deadline-driven, cheapest-slots-first math (`ems/ev_planner.py`), the same shape as the winter-arbitrage deadline planning in §8.9 but applied to the car: `E_i = max(0, (min_pct_i − soc)/100 × capacity)`, allocated earliest-deadline-first into the cheapest usable slots before each `ready_by`, with a sunny slot valued at its feed-in export price (§8.3's `export_value`) rather than the full price. Worked math + brute-force cost-optimality cross-check: [`docs/superpowers/specs/2026-07-12-ev-charging-design.md`](docs/superpowers/specs/2026-07-12-ev-charging-design.md). Car SoC is **not read from the vehicle**: the user sets a manual **anchor** (%, timestamp, via `POST /api/car/soc`), and the estimate then rises only from kWh the HomeWizard car meter measures while charging (`ems/ev_session.py`) — **driving is not modeled**, so the user re-anchors after a trip, and an estimate older than 72 h is flagged stale. This is **advisory/visual only in v1** — it never commands a charger or the car (there is no charger API); **charger/car *control* stays out of scope until [`docs/v2-ev-control.md`](docs/v2-ev-control.md) is written** (§6.4). The existing car-guard (§4.5 — never discharge the home battery into the car) is completely untouched by this feature and is pinned by a dedicated regression test.

**Platform & ML (per [`GOAL.md`](GOAL.md)):** the core runs CPU-only on a **Raspberry Pi**; an **accelerator** (CUDA on a **Jetson**, Metal/CoreML/MLX on **Apple Silicon**) lights up the **optional ML layer** (load forecasting, a learned planner, local-LLM explainer) behind `ports.py`, selected by the planner-mode switch and never bypassing the §8.11 validator. The **explainer** is independent (not accelerator-gated): `template` / `local_llm` / `external_llm` (cloud, opt-in). Spec: [`docs/ml-layer.md`](docs/ml-layer.md); deploy: [`docs/jetson-deployment.md`](docs/jetson-deployment.md), local dev §11.6.

**Language = Python (deliberate).** Chosen because the integration ecosystem (Home Assistant, Indevolt/HomeWizard/Tibber/Solcast) and the ML ecosystem (PyTorch/ONNX/Core ML/llama.cpp) are Python-native, and the workload is a 5-min I/O-bound mode-switching loop where runtime speed is irrelevant — so KISS/velocity wins. The stack is healthily polyglot at the seams (Python backend · React/TS frontend · ML sidecar). Go (single-binary daemon) or TS-everywhere were considered and rejected: both lose on the ML + energy-integration ecosystems.

**Optional / future enhancements (documented, not core scope — [`docs/control-model.md`](docs/control-model.md) §13):**
- **Time-of-day forecast correction** — per-hour correction factors (still clamped) instead of one daily `k`.
- **Away/vacation mode** — bias toward low cost + high reserve while away.
- **Storm / outage reserve mode** — on a weather alert or manual flag, hold a high reserve.
- **Learning freeze** — exclude flagged unusual days from the consumption baseline so they don't distort it.
- **Weekly export report** — one week of plans, actions, savings, and warnings (UI export, §9.1).

## 17. Known uncertainties (owner · action · evidence required)

| # | Unknown | Owner | Action (when) | Evidence required to close |
|---|---|---|---|---|
| 1 | Cluster max charge/discharge W | Jeroen | M1a probe | HA power-sensor max / `Indevolt.GetData` value |
| 2 | Whether `indevolt.charge/discharge` take `power` + `target_soc` (exact schema) | Jeroen | M1a probe | HA service schema dump |
| 3 | Energy-mode **select** options (which = self-consumption) | Jeroen | M1a probe | select entity options list |
| 4 | True IDLE/hold available? (standby button vs emulate) | Jeroen | M1a/M1b | observed SoC holds after standby press |
| 5 | Which kWh meter is solar vs car; each meter's sign | Jeroen | M0a | labelled meter reads vs known load |
| 6 | Tibber `total` includes all grid fees? | Jeroen | M0c | tariff breakdown vs invoice |
| 7 | Quarter-hourly `priceInfoRange` returns NL data for this account | Jeroen | M0c | non-empty QUARTER_HOURLY response |
| 8 | `usable_kwh` ceiling (push >9.7?) | Jeroen | M1b | full-range SoC test |
| 9 | `summer_solar_threshold_kwh` calibration | Jeroen | M0c | PVGIS monthly yield + a few logged days |
| 10 | `degradation_cost_eur_per_kwh` realistic value | Jeroen | M2 | cell warranty cycles / cost-per-kWh-cycled |
| 11 | **Is the Indevolt paired with / reading the P1 meter?** | Jeroen | M1a probe | `p1_paired` confirmed; grid≈0 in self-consumption |
| 12 | **Does P1 zeroing stay active per mode?** (the §2 contract) | Jeroen | M1a/M1b | observed grid flow in AUTO/CHARGE/DISCHARGE/IDLE |
| 13 | Standby/hold distinct from "self-consumption disabled"? | Jeroen | M1a probe | both entities present + observed behaviour |
| 14 | Is the battery already in a vendor schedule/manual mode? | Jeroen | M1a probe | current energy-mode/state read |
| 15 | Season `target_soc_ceiling` values | Jeroen | M2/M3 | longevity guidance + observed degradation |
| 16 | Jetson JetPack/L4T version + available VRAM | Jeroen | M6 / Jetson setup | `nvidia-smi`/JetPack report on the device |
| 17 | Which ML models fit the Jetson VRAM (forecaster + optimizer + LLM) | Jeroen | M6 | models load + run within the VRAM budget |
| 18 | Remote-HA latency tolerable from the Jetson over LAN | Jeroen | Jetson setup | WS round-trip vs the 300 s cycle |

## 18. Supporting documents

- [`docs/api-reference.md`](docs/api-reference.md) — concrete endpoint/auth cheat-sheet (incl. **exact Tibber quarter-hour GraphQL query**). HA service/entity examples are filled in **after the M1a probe confirms them**.
- [`docs/energy-model.md`](docs/energy-model.md) — sign conventions, reconstruction, **data dictionary** for every internal metric, calibration procedure, control-cycle & replan **sequence descriptions**.
- [`docs/config-reference.md`](docs/config-reference.md) — full per-key reference (type, range, default, effect), separate from the §9 sample.
- [`docs/failure-modes.md`](docs/failure-modes.md) — failure-mode table: missing prices, missing forecast, HA down, battery unreachable, Solcast budget exhausted, clock skew, meter stale.
- [`docs/operator-runbook.md`](docs/operator-runbook.md) — disable EMS, force AUTO, inspect the last decision, rotate a token, restore a backup.
- [`docs/control-model.md`](docs/control-model.md) — the **control plane**: P1-zeroing contract, `BatteryIntent`→mode→vendor mapping, `CapabilityReport`, target-SoC math + energy-unit definitions, deadline planning, the `Plan` object + validator, ownership state machine, missed-window recovery, data-quality.
- [`docs/ml-layer.md`](docs/ml-layer.md) — the **optional ML layer**: the `LoadForecaster`/`Planner`/`Explainer` ports, the `rule_based`/`ml`/`advisory` planner-mode switch, the "ML proposes, validator disposes" contract, on-device training, serving runtimes/budgets, the local-LLM explainer, and fallback detection.
- [`docs/jetson-deployment.md`](docs/jetson-deployment.md) — the **Jetson deployment** variant: EMS + ML on the Jetson with **HA on the LAN**, NVIDIA container runtime, the lean-EMS-image vs GPU-ML-sidecar split, and GPU capability detection.
- [`docs/v2-ev-control.md`](docs/v2-ev-control.md) — *(placeholder stub; v2, not started)* the EV-control specification scope (auth, BLE/cloud options, safety, UX).

---

## 19. Document history

- **Iteration 1 — Write.** Full draft from domain knowledge.
- **Iteration 2 — Review.** Reconciled component sections against June-2026 research; worked examples + control-loop pseudocode.
- **Iteration 3 — Improve.** Corrected figures, Solcast-polling nuance, tightened consistency; clarified plan+spec dual role.
- **Iteration 4 — Validation pass (this revision).** Acted on an external spec validation:
  - **Correctness:** status → implementation-ready draft + validation checklist + per-integration verification + known-uncertainties table.
  - **Energy model (new §4):** P1 is **net grid**, not house load; reconstruction formula; sign conventions; raw-vs-derived storage; calibration phase; precise EV exclusion; missing-meter fallback.
  - **Battery (§6.5):** corrected the HA command surface (charge/discharge are the only services; standby=button, energy-mode=select) against the official HA docs; **capability probe**; HA-vs-RPC by capability; IDLE emulation; discharge capped by load; min dwell; idempotency; confirmation; failure→AUTO; manual-change tracking.
  - **Prices (§6.2):** corrected `priceInfoRange` placement (under `currentSubscription`) against the Tibber schema; exact query in api-reference; immutable-slot caching; both resolutions + hourly→15-min expansion; completeness validation; freshness rules; negative prices/export tariffs; grid-fees policy.
  - **Arbitrage/summer (§8):** economics formula + degradation/risk/cycle budget; serve-load not dump; no fixed-95% fill; SoC projection in the planner; evening-peak reservation; no-trade mode + hysteresis; deficit-only summer top-up; avoid-precharge-before-solar; midday-negative-price policy; seasonal hysteresis; roof-calibrated threshold.
  - **Forecast (§6.3):** Forecast.Solar is rate-limited (not "uncapped"); single refresh owner (EMS) + budget ledger; provenance; bounded rolling correction.
  - **HA/UI/Deploy/Security/Testing/Build:** entity-map + startup validation; freshness/alerts; dashboard vs setup split; dry-run/live badge; why-not + override-with-expiry + export; OSM/Nominatim policy; health endpoints, graceful shutdown, backups, log/DB maintenance, resource limits, HA-Container add-on note; security section; full test matrix incl. DST + property tests + dry-run gate; M0/M1 split with read/probe-before-write; EV moved to a separate v2 spec.
- **Iteration 5 — Control-plane pass (this revision).** Acted on a second validation focused on control architecture (new [`docs/control-model.md`](docs/control-model.md)):
  - **Intent layer (§7.1):** `BatteryIntent` (allow-self-consumption / grid-charge-to-target / hold-reserve / discharge-for-load) → physical mode → probe-resolved vendor action; compatibility matrix with a **"P1-zeroing active?"** column; per-action **preconditions**.
  - **"Indevolt owns P1 zeroing — don't fight vendor control" (§2):** elevated to a design constraint; verified-and-stored per mode at M1 (§6.5, §17), not assumed.
  - **Battery (§6.5):** `CapabilityReport`; paired-meter check; vendor-schedule detection + `takeover_policy`; capture/restore original mode; standby vs self-consumption-off distinction.
  - **Planning (§8.9–§8.12):** target-SoC + deadline planning (sunset/morning); usable-now & remaining-day-solar; season SoC ceilings; min/max grid charge; operating policies (solar-first/economy-vs-comfort); the validated, **versioned `Plan`** + plan validator + confidence/data-quality badge + invalidation/replan rules; **charge-completion** + **missed-window recovery**; planned-vs-actual SoC deviation replan.
- **Iteration 6 — Observability & data pass.** Made production behaviour measurable after the fact (new tables in §4.3, export package in §9.1): day-ahead **solar forecast logged vs. actual** (`forecast_snapshots`) with a **forecast-skill** read (bias / MAE / band-coverage, `ems/analysis.py`) in the export; **per-cycle plan history** (`plan_history`: strategy / target-SoC / deadline / achieved-SoC / intent); a **battery-power plausibility clamp** on ingest; a control-health **incident rollup** (command failures / mismatches / fallbacks / reverts) in the System UI and export; **daily-finance backfill** for a gapless window; and **P1 gas ingestion** (`total_gas_m3` → `gas_readings`) folded into the CO₂ score. New endpoints `GET /api/export/package` and `GET /api/incidents`. Export stays **redacted** (allowlisted config keys — no tokens/IPs/location).
  - **Data quality (§4.7):** per-signal staleness, source priority, plausibility checks, timestamp/DST hygiene.
  - **Runtime (§13.1–§13.4):** domain objects defined early; tz-aware slot utilities; **ownership state machine**; boot sequence (observe→validate→load→maybe act); startup grace; persisted plan id/version, last-action, daily switch counter, unresolved warnings.
  - **UI (§9.1):** intent + ownership + data-quality badges; "why is EMS not charging?" diagnostic panel; controls (return-to-default / pause-until-tomorrow / force-charge-target); setup wizard + first-run dry-run summary; weekly report.
  - **Testing (§14):** fake Indevolt adapter; scenario runner + golden fixtures; target-SoC/feasibility, restart-during-charge, deviation-replan, missed-window, and plan-validator tests.
  - **Scope:** away/storm/vacation, learning-freeze, time-of-day forecast correction, weekly report logged as **documented optional/future** (§16).
- **Iteration 6 — Goal reconciliation (this revision).** Brought the spec in line with [`GOAL.md`](GOAL.md) after a third validation, and fixed correctness issues it surfaced:
  - **UI → React + Vite (§9.1, §9 config, §13):** replaced the vanilla/vendored plan with a bundled SPA built at image-build time and served by FastAPI (SPA fallback); no runtime CDN (charts/Leaflet/fonts/icons bundled); bundle-size budget, WCAG 2.1 AA, light/dark, English-only-v1; Playwright + visual-regression as the §6 visual-test gate.
  - **Optional ML layer (§2, §8 intro, §13, new [`docs/ml-layer.md`](docs/ml-layer.md)):** softened the ML non-goal to "no ML in the *core*"; added the `LoadForecaster`/`Planner`/`Explainer` **ports** and the runtime **planner-mode switch** (`rule_based`/`ml`/`advisory`); the **"ML proposes, the §8.11 validator disposes"** contract; on-device training, serving budgets, local-LLM grounding, and per-capability fallback; added an **M6** milestone.
  - **Jetson (§11, header, new [`docs/jetson-deployment.md`](docs/jetson-deployment.md)):** added the Jetson variant — EMS + ML on the Jetson, **HA on the LAN** — with NVIDIA container runtime, a lean-EMS-image vs GPU-ML-sidecar split, and GPU capability detection; same codebase, two targets; §17 Jetson/ML uncertainties.
  - **Correctness fixes from the validation:** §8.6 worked SoC cascade made consistent + respecting the 90% winter ceiling; §6.2 DST counts reordered (96/92/100); the **plan validator** now takes the *remaining* same-day switch budget and min-dwell as inputs (§8.11); **discharge "serve load"** clarified to rely on vendor self-consumption, not a power-tracking loop (§8.3); evening-reserve and equivalent-cycle defined; `allow_solar_charge` config-label corrected.
- **Iteration 7 — Accelerators + external explainer (this revision).**
  - **Accelerator-agnostic ML:** generalized the GPU gate from CUDA-only to **any supported accelerator** — CUDA (Jetson), **Metal/CoreML/MLX (Apple Silicon)**, CPU fallback. `require_gpu` → **`require_accelerator`**; runtimes set to `auto`; `capabilities.py` detects the best backend. Apple Silicon is now a first-class ML dev host (§11.6), with the caveat that Docker-on-macOS has no GPU passthrough → the ML sidecar runs **natively** (same localhost-sidecar pattern as the Jetson).
  - **Explainer decoupled + external option:** the **`Explainer`** is its own top-level config block with three backends — `template` (offline, default), `local_llm` (accelerator), and **`external_llm`** (a cloud LLM API, e.g. MiniMax) which **works on a plain Pi**. `external_llm` is **off by default, opt-in**, sends a **minimal redacted payload**, never touches control, falls back to the template, and its API key is a secret (privacy/security §12). Touched §2, §6.5/§7.1, §9 config, §9.1/§9.3, §11.6, §12, §13, §15, ml-layer.md, jetson-deployment.md, config-reference.md, CLAUDE.md, README.md, GOAL.md.
- **Iteration 8 — Post-2027 economics pass.** Export-price-aware valuation in the new pure `ems/planner/economics.py`: shared `breakeven()` (factored out of the arbitrage gate, both planners behaviour-identical) + `export_value()` with `net_metering` (default, today's *saldering*) / `spot_minus_tax` (post-2027, may go negative, unclamped) / `fixed` models; `day_finance` credits export per model in **both** the actual and the no-battery baseline. Added the opt-in **negative-price soak** (`planner.negative_price_soak`, default **off**) across the winter, adaptive and summer planners — charge when the price is below €0, with a plain "you are paid to charge" reason and a plan-level "+N negative-price slots" note. Replaced the old `midday_negative_price_action` sketch with the implemented bool. Touched §8.2/§8.3, §9 config, config-reference.md.
- **Iteration 9 — EV charging advice pass.** Shipped the v1 EV-charging-advice feature (design [`docs/superpowers/specs/2026-07-12-ev-charging-design.md`](docs/superpowers/specs/2026-07-12-ev-charging-design.md)): a static EU car database (`ems/cars.py`), a weekly per-day minimum-SoC schedule (`ems/ev_schedule.py`), charging-session detection + a manual-anchor SoC estimate (`ems/ev_session.py`), and a pure deadline-driven, cheapest-slot-first charge planner (`ems/ev_planner.py`) — new `GET /api/cars`/`GET /api/car/plan`/`POST /api/car/soc`, a dashboard + iOS Car card, and an `ev_sessions.csv` export line. Advisory/visual only, no charger/car control (§16, `docs/v2-ev-control.md` updated from a placeholder to reflect the v1 advisory layer); the existing car-guard (§4.5) is untouched, pinned by a dedicated regression test. Touched §9.1, §16, config-reference.md, v2-ev-control.md.
- **Iteration 10 — Winter-proofing (B-15 + B-22 + B-16).** **Seasonal-transition hysteresis** (§8.4): the `auto` season pick is now dampened by `resolve_strategy_hysteretic`/`HysteresisState` — a change must hold `strategy.hysteresis_days` (default 3; 0 disables) consecutive daily evaluations before it commits, so shoulder-month days can't flap; fresh/absent state behaves exactly like the prior instantaneous pick, the counter is KV-persisted (restart-safe) and threaded through `replay.py`; the summer **sunset deadline** was confirmed already forecast-derived (`sunset_after`, deliberately not `astral`). **Projected-SoC gate** (§8.5/§8.11): the pre-apply validator now reuses `projection.py` to reject an unreachable grid-charge plan (>5 pp short of `target_soc` by `deadline`) — `unsafe` ⇒ fail safe to `AUTO`; conservative, data-quality-aware (`complete` only), behind `planner.validate_projection` (default on). **Missed-window recovery** (§8.12): new pure `planner/recovery.py` catches up a missed cheap charge window in the cheapest remaining slots before the deadline (honest partial when the hours run out), folded into `_current_plan` so it passes the same §8.11 validator + control caps and never fights the hysteresis, behind `planner.recovery_enabled` (default on); audited + calmly notified, one recovery per window per day. Touched §8.4, §8.5, §8.11, §8.12, config-reference.md.
- **Iteration 11 — Car-charging battery modes, UI (this revision, feat/car-charge-modes).** The car-guard (§4.5) now offers three operator-chosen behaviours instead of only holding — `hold` (default, unchanged) / `static_discharge` (fixed W, names the physics when it overshoots the house load) / `match_home_load` (battery covers the predicted non-EV house load) — via `control.car_charging_battery_mode` + `control.car_discharge_w`, routed through the narrow `car_session` `DISCHARGE` mapping (§7.1) with a bounded recommand rule, a ≥10-minute dwell and a 6-command session cap so it stays a mode-switch, not a power-tracking loop. Shipped a Car-tab "While the car charges" section (three keyboard-accessible radio cards + the reworded master toggle, moved out of Settings, immediate-save) and a mode-aware dashboard badge (previously a static "battery held" regardless of mode). Touched §4.5, §7.1, config-reference.md.

*End of specification.*
