# API Reference — devices & services

Quick, concrete cheat-sheet for every integration. Details and rationale are in `../SPEC.md` §6. **Verification is per-integration:** public API shapes (Tibber GraphQL, HomeWizard local API, Forecast.Solar) are checked against vendor docs; **device-/account-/firmware-specific values are runtime-specific** and tagged **CONFIRM@M0/M1** (see `../SPEC.md` §17). Re-check vendor docs before relying on a specific field.

---

## Indevolt battery — OpenData API (local)

- **Model:** SolidFlex 2000, Gen-2, 2-tower cluster. Control the cluster as **one** device.
- **Primary control path = Home Assistant** (official integration, repo `github.com/INDEVOLT/homeassistant-indevolt`). **Resolve the exact surface with the M1a capability probe** (`../SPEC.md` §6.5) — the integration exposes **fewer services than you might assume**:
  - **Services (the only two):** `indevolt.charge` and `indevolt.discharge` — each runs *until a target SoC*. Treat `{power: W, target_soc: %}` as **candidate** params and **confirm the schema at the probe**.
  - **No `indevolt.stop`, no `indevolt.change_mode`.** Those are **entities**, not services:
    - **Standby / idle hold** → a **button** entity ("Enable standby mode").
    - **Self-consumption / mode** → a **select** entity ("Energy mode") — read its options at the probe.
    - **Discharge floor (min SoC)** → a **number** entity ("Discharge limit").
    - **Max AC output / feed-in / inverter input limit** → **number** entities (Gen-2).
    - **Grid charging** → a **switch** entity ("Allow grid charging"). Plus bypass/LED switches.
  - SoC/power exposed as **sensors**. `battery.py` builds its mode→action mapping from what the probe finds (`../SPEC.md` §6.5).
- **Fallback = direct RPC** (enable local API in the Indevolt app first):
  - `POST http://<ip>:8080/rpc/Indevolt.GetData` (read) · `…/Indevolt.SetData` (write) · `…/Sys.GetConfig`
  - Auth: **HTTP Digest** (user `opend` + device key).
  - **Mode** = data point `47005`: `1` self-consumption · `4` real-time control · `5` ToU schedule · `0` outdoor.
  - **Real-time control** (mode 4), write together: `47015` state (`0` idle / `1` charge / `2` discharge) · `47016` power W (≈50–2400, model-dependent — read real max from GetData) · `47017` target SoC (5–100 %).
- **Rate limit:** ≥ 5 s between writes (1 s min). **Not** for continuous modulation.

## HomeWizard — local API

- Discover via mDNS `_hwenergy._tcp.local`. `GET http://<ip>/api` → `product_type` (branch on it; pin one API version per device).
- Your devices: grid = **P1 (`HWE-P1`)**; solar & car = **kWh meters (`HWE-KWH1/3`)**.
- **v1 (token-less):** `GET http://<ip>/api/v1/data`. P1 raw telegram: `GET /api/v1/telegram`.
  - Fields: `active_power_w` (P1 signed: + import / − export), per-phase `active_power_lN_w`, `total_power_import_kwh` (+ `_t1/_t2`), `total_power_export_kwh`, voltages/currents, gas `total_gas_m3`.
- **v2 (recommended):** HTTPS + **bearer token**. Token: press button → `POST https://<ip>/api/user {"name":"local/ems"}`. Measurements `GET https://<ip>/api/measurement`. v2 **drops prefixes** (`power_w`, `energy_import_kwh`).
- **Polling:** ≥ 500 ms; power refresh ~1–60 s, gas 5–60 min.
- HA: official **HomeWizard Energy** integration (mDNS auto-discovery).
- **Sign convention & house load:** the **P1 is *net grid flow*, not house load** (+ import / − export). House load is **reconstructed**: `house_load = grid + solar + battery_power` (battery: + discharge / − charge). See `../docs/energy-model.md`. Confirm each meter's native sign at M0; fall back safely if a meter is missing/stale.

## Tibber — GraphQL (cloud)

- `POST https://api.tibber.com/v1-beta/gql` · header `Authorization: Bearer <token>` (from `developer.tibber.com/settings/accesstoken`).
- **Hourly prices** (`today`/`tomorrow` are *hourly*): `viewer.homes[].currentSubscription.priceInfo` → `current` / `today[]` / `tomorrow[]`, each `{ total, energy, tax, startsAt, level, currency }`. `energy` = Nord Pool spot; `total` = energy + tax (**may not include all grid/transport fees** — confirm vs your tariff). `level` = `VERY_CHEAP…VERY_EXPENSIVE`.
- **15-min prices (NL since 2025-10-01) — placement corrected.** `priceInfoRange` is **a field of `currentSubscription`** (not a top-level `viewer` query). Required arg `resolution` (`QUARTER_HOURLY` | `HOURLY` | `DAILY`) + pagination (`first`/`last`/`before`/`after`). **Capped at 672 quarter-hours (7 d) / 744 hours (31 d) / 31 days.** Tomorrow's prices land ~**13:00 CET** (poll with jitter). Cache past slots — Tibber prices are immutable, so never re-fetch them.

  **Exact quarter-hour query (today + tomorrow's forward window):**
  ```graphql
  {
    viewer {
      homes {
        currentSubscription {
          priceInfoRange(resolution: QUARTER_HOURLY, first: 192) {
            nodes {
              total
              energy
              tax
              startsAt
              level
              currency
            }
          }
          priceInfo {                 # hourly fallback / cross-check
            current { total startsAt level }
            today    { total startsAt level }
            tomorrow { total startsAt level }
          }
        }
      }
    }
  }
  ```
  *(`first: 192` ≈ 48 h of quarter-hours; raise toward the 672 cap if you want more horizon. Verify the connection field names — `nodes`/`edges` — at first call; if QUARTER_HOURLY is empty for the account, fall back to `priceInfo` hourly and **expand each hour into 4 identical 15-min slots**.)*
- Live power (optional, needs Pulse): `wss://websocket-api.tibber.com/v1-beta/gql/subscriptions` (`graphql-transport-ws`), `subscription { liveMeasurement(homeId) { power ... } }`.
- **Free no-key fallback / cross-check (not the default provider):** HA **EnergyZero** integration (NL day-ahead hourly, tomorrow ~14:00). Also ENTSO-E (free token) / Nord Pool.

## Solar forecast

- **Solcast (primary, free Hobbyist):** account → **10 calls/day** (new accounts; older = 50). `GET https://api.solcast.com.au/rooftop_sites/{resource_id}/forecasts?format=json` → `pv_estimate` (kW, P50), `pv_estimate10/90`, `period_end`. 7-day, 30-min. **The EMS owns the refresh** on a daylight schedule and keeps a **daily call-budget ledger** (resets at local midnight) so a retry/refresh loop can't exhaust the 10/day budget. Store each forecast's **issue time + provider**.
- **Forecast.Solar (fallback, keyless — rate-limited, NOT "uncapped"):** `GET https://api.forecast.solar/estimate/{lat}/{lon}/{tilt}/{azimuth}/{kwp}` → `watts`, `watt_hours_period`, `watt_hours_day`. **Limit ~12/hr per IP**, 1 plane, today+tomorrow, hourly. Azimuth: **0 = south** (raw API); **HA UI uses 180 = south**.
- **Open-Meteo (documented optional fallback only — out of core scope):** `global_tilted_irradiance` w/ `tilt`/`azimuth` (0=south). `PV_kWh = GTI/1000 × kWp × PR` (PR ≈ 0.80). ~10k calls/day, CC-BY. Add only if Forecast.Solar proves insufficient.
- **PVGIS:** one-off annual baseline (not a forecast).

## Tesla Model Y (v2, optional control)

- Commands: `charge_start`, `charge_stop`, `set_charging_amps`, `set_charge_limit`. Model Y **requires Vehicle Command signing**.
- Options: **Tesla BLE** (ESP32 + `yoziru/esphome-tesla-ble`, local, no fees — best for a HEMS) · **Tessie** (~$13/mo, HA core, easiest) · **Teslemetry** (~€32/mo) · **Tesla Fleet API** (free ≤ $10/mo credit, self-host the HTTP proxy).
- Caution: amp changes wake the car; cloud ≈30 cmd/min; `set_charging_amps` min/max undocumented — debounce + min dwell, read bounds at runtime.
- v1: EV is read-only via the **HomeWizard car meter** (no Tesla credentials needed).

## Home Assistant (the EMS talks to HA)

- WebSocket `ws://<host>:8123/api/websocket` (auth → `subscribe_entities` / `call_service`) — preferred for live state, for calling the `indevolt.charge`/`discharge` **services**, and for setting the Indevolt **entities** (energy-mode select, standby button, discharge-limit number, grid-charge switch). REST as fallback.
- **Entity mapping:** pin role→entity ids in config `entity_map` (don't rely only on discovery names); validate they exist with sane `state_class`/units at startup (`../SPEC.md` §5.2, §11.5).
- EMS → HA entities via **MQTT discovery** (`homeassistant/<component>/<object_id>/config`, with `unique_id` + `device`). **Retain** discovery **config** topics (survive restarts); retain slow state (mode/strategy/reason), don't retain fast telemetry. Do **not** use `POST /api/states` for durable entities (transient, lost on restart).
