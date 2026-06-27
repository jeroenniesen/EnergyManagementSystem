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
| Prices | Tibber GraphQL | ⚠️ adapter ready | the supplied token is **rejected** (`UNAUTHENTICATED`); set a valid `TIBBER_TOKEN`. Falls back to empty (logged) until then. |
| Battery power + SoC | Indevolt OpenData RPC | ⚠️ adapter ready | `Indevolt.GetData` is reachable but returns `{}`: **enable the OpenData data points in the Indevolt app and supply `INDEVOLT_KEY`** (HTTP Digest, user `opend`). Confirm the SoC/power register addresses against a provisioned device. |

When the battery is unreadable, `soc`/`battery` age to MISSING, data-quality goes **unsafe**, and
the decision falls back to **AUTO** (self-consumption) — fail-safe by design. You can watch this on
the **System** page (per-signal sensor checks) and the dashboard freshness chips.

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
