# Live device integration (read-only senses)

How to run the EMS against the real devices. **Everything here is read-only — the EMS never
commands the battery.** Two independent safeguards: there is no live battery *writer* in the
codebase (the controller always uses the mock driver), and live mode forces `dry_run=true`.

## Enable live mode

```bash
# read the real devices defined in config.yaml (devices: block)
EMS_SOURCES=live uv run uvicorn ems.main:app --host 0.0.0.0 --port 8080
```

`config.yaml` holds the LAN addresses (not secrets):

```yaml
sources: { mode: mock }        # or: live  (env EMS_SOURCES=live overrides)
prices:  { provider: mock }    # or: tibber (env EMS_PRICES=tibber)
devices:
  p1_ip: 192.168.50.92         # HomeWizard P1 (net grid)
  solar_ip: 192.168.50.37      # HomeWizard kWh (solar)
  car_ip: 192.168.50.98        # HomeWizard kWh (EV)
  indevolt_ip: 192.168.50.53   # Indevolt main tower (cluster = one logical device)
```

Secrets are env-only, never committed: `TIBBER_TOKEN`, `INDEVOLT_KEY`, `EMS_WEB_TOKEN`.

## Status (verified 2026-06-28)

| Sense | Source | Status | Notes |
|---|---|---|---|
| Grid (net) | HomeWizard P1 `/api/v1/data` | ✅ **live** | `active_power_w` = +import/−export, used directly |
| Solar | HomeWizard kWh | ✅ **live** | production = magnitude of `active_power_w` |
| EV load | HomeWizard kWh (3-phase) | ✅ **live** | `max(0, active_power_w)` |
| Prices | Tibber GraphQL | ✅ **live** | verified end-to-end — 96 quarter-hourly slots feed the planner (token note: the value includes a trailing `-1`; it is NOT pure hex). |
| Battery power + SoC | Indevolt OpenData RPC | ⚠️ adapter ready | `Indevolt.GetData` is reachable but returns `{}`: **enable the OpenData data points in the Indevolt app and supply `INDEVOLT_KEY`** (HTTP Digest, user `opend`). Confirm the SoC/power register addresses against a provisioned device. |

Status (2026-06-28): **2/3 device groups live** — HomeWizard meters + Tibber prices both verified
end-to-end through the running server (planner now computes on real prices). Indevolt is the only
remaining gap: **Modbus TCP/502 is refused** on both towers, so the OpenData RPC on :8080 is the
only interface — and it returns `{}` until the data points are provisioned + the Digest device key
is supplied (which the consumer app may not expose; could need Indevolt support).

When the battery is unreadable, `soc`/`battery` age to MISSING, data-quality goes **unsafe**, and
the decision falls back to **AUTO** (self-consumption) — fail-safe by design. You can watch this on
the **System** page (per-signal sensor checks) and the dashboard freshness chips.

## Unblocking the Indevolt battery read — what to ask Indevolt (or find in the app)

The local API on `192.168.50.53:8080` responds to **only** `Indevolt.GetData`, and it returns `{}`
for every `config` value tried (Modbus/502 is closed). To read SoC/power I need three things:

Diagnosis (2026-06-28): the local API is **already enabled** — `GET /rpc/Sys.GetConfig` returns
the device identity unauthenticated (`type: CMS-SF2000, sn: 3301958491, fw V1.4.0C…`). So there is
no "OpenData" toggle to find in the app; the device is reachable. What's missing is the **API
documentation**: `Indevolt.GetData` requires a `config` parameter and returns `{}` for every value
tried (and there's no other data-returning method), so I can't guess the data-profile syntax or the
SoC/power register ids. That comes from Indevolt's OpenData API spec, not an app setting.

> **Request to Indevolt support / installer (include device sn 3301958491, type CMS-SF2000):**
> "I've enabled the local OpenData API on my SolidFlex cluster (`Indevolt.GetData` on port 8080
> responds). I want to **read** battery State of Charge and instantaneous power over the local API
> (read-only — no control). Please tell me:
> 1. the **OpenData device key** for HTTP Digest user `opend` (and how to (re)generate it),
> 2. the **`config` / data-profile value** to pass to `GET /rpc/Indevolt.GetData?config=<…>` so it
>    returns data (it currently returns `{}` for `all`, `battery`, register lists, etc.),
> 3. the **data-point / register ids for SoC (%) and battery power (W)** in that response."

Once you have the **key** and **config name**, drop them in and verify (read-only):
```
INDEVOLT_KEY='<key>' INDEVOLT_CONFIG='<config-name>' uv run python scripts/verify_live.py
```
If the SoC/power register ids differ from the defaults, set them via the driver `registers=`
mapping (or tell me the ids and I'll wire them).

## Hands (battery control)

The real control driver — `IndevoltBatteryDriver` (`ems/sources/indevolt_driver.py`) — IS
implemented and wired into the live control loop. It maps a `PhysicalMode` to the documented
SetData registers (mode 47005 · state 47015 · power 47016 · target-SoC 47017) and confirms each
write by re-reading. The full chain (plan intent → `ModeController.decide()` → `driver.apply()` →
correct `SetData`, confirmed) is unit-tested end-to-end against a mock device.

It is **triple-gated so it cannot change your battery** in the shipped wiring:
1. `armed=False` — `apply()` refuses and returns "unconfirmed" without writing.
2. No write transport — `rpc_post` defaults to a stub that raises; a live write requires the
   operator to inject a real POST transport (main.py injects none).
3. `dry_run` is forced on for live, so `decide()` never reaches `apply()`.

To actually control the battery you would: provision Indevolt OpenData + supply `INDEVOLT_KEY`,
inject a real SetData transport, construct the driver with `armed=True` (the flag is read-only
after construction), and lift `dry_run` — a deliberate, vetted, operator-armed step. **I did not
do this** (you asked me not to change the battery).

Arming follow-up: `apply(mode)` currently targets 100% SoC on CHARGE/DISCHARGE; before arming,
wire the plan's target SoC / power through to `setdata_registers` (it already accepts them).

## What's intentionally NOT done

- **No live battery write was ever issued** — by design (the triple gate above).
- **Solar forecast stays mock** (no Solcast key / configured lat-lon yet).
- Daylight sign conventions for solar/EV were validated at night (both ~0 W); confirm the solar
  sign during production (the adapter takes the magnitude, which is correct for a one-way PV meter).

## Tests

Unit tests use **recorded device payloads** — no hardware in the test suite (CLAUDE.md). Live
verification is done by running the probe/server manually against the device IPs.
