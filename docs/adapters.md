# Adapters — developer notes

Short notes for anyone adding or reviewing a vendor adapter. The full "write a new adapter"
guide lands with [#125](https://github.com/jeroenniesen/EnergyManagementSystem/issues/125);
each epic-#111 slice keeps this file current for its own invariants.

## Safety invariants (epic #111)

| # | Invariant | Guard test | Issue |
|---|---|---|---|
| **I1** | Only `ModeController.decide` and `CommandExecutionBoundary.apply` call `apply` on a battery driver. | `ems/tests/test_architecture_guards.py::test_i1_battery_driver_apply_only_via_allowed_sites` (AST scan) | [#138](https://github.com/jeroenniesen/EnergyManagementSystem/issues/138) |
| **I2** | Modules under `ems.sources.*` do not import `ems.control`, `ems.planner.validator`, or `ems.settings`. | `ems/tests/test_architecture_guards.py::test_i2_sources_do_not_import_control_validator_or_settings` | [#138](https://github.com/jeroenniesen/EnergyManagementSystem/issues/138) |
| I3 | `armed` and transport are injected, with a refusing default. | Per-adapter spy-transport (unarmed / dry-run → 0 calls) | [#139](https://github.com/jeroenniesen/EnergyManagementSystem/issues/139), #114 |
| I4 | Power and SoC are centrally clamped from capabilities before `apply`. | Spy-transport capability bounds | #112 slice c, #114 |
| I5 | `armed` is required on the port; no `getattr` on the shutdown path. | Shutdown test with a fake adapter | [#127](https://github.com/jeroenniesen/EnergyManagementSystem/issues/127) |

I1, I2, and I3 run in the ordinary `pytest` suite (hermetic, no network, no live Indevolt). They
replace the epic's manual grep/review checklist. Exceptions must be listed in the test's
allowlist with a reason (and, for I2, an issue that will remove them).

### I1 — single battery writer

Startup wiring returns a named `Wiring` (`ems/connection.py`) so the battery driver handle is an
explicit field, not a positional slot. Runtime writes still go only through:

1. `ems/control/mode_controller.py` — `ModeController.decide` → `driver.apply(...)`
2. `ems/control/execution.py` — `CommandExecutionBoundary.apply` → `controller.driver.apply(...)`

Any new production `*driver.apply(` under `ems/` outside those files fails CI
unless it is added to `_I1_ALLOWLIST` with a documented reason. Matching is by name
suffix (`endswith("driver")`), so `controller_driver.apply(...)`,
`wiring.controller_driver.apply(...)`, and `self._driver.apply(...)` are caught as
well as the literal `driver` / `.driver` forms. Known AST blind spots (not a proof):
`getattr(driver, "apply")(...)`, `importlib.import_module("ems.control")`, and
calls through an arbitrary alias (`d = self.driver; d.apply()`).

### I2 — adapters stay below the control layer

Source adapters sense devices and expose ports. They must not pull in the control loop, the plan
validator, or the settings schema — that keeps vendor code from bypassing fail-safe / dry-run
gates. The AST import scan covers every module under `ems/sources/`.

### I3 — one battery port + arming / spy-transport

There is **one** `BatteryDriver` protocol: `ems/sources/ports.py`, re-exported from
`ems/ports.py` and from `ems/application/protocols.py` (no second, narrower copy). Required
members today:

| Member | Role |
|---|---|
| `armed` | Read-only property; refuse-by-default. Required on the port (#127 / I5). |
| `probe()` | Capability report (read-only). |
| `configure_power_limits(*, max_charge_w, max_discharge_w)` | Align advertised limits with settings — called directly from the API (no `getattr`). |
| `current_mode()` | Observed physical mode. |
| `apply(mode, *, target_soc, power_w)` | The write path; gated by armed + transport + dry-run. |

Triple-gate (still unchanged by this slice):

1. **armed** — driver constructor defaults to `False`; no setter.
2. **transport** — Indevolt refuses without an injected `rpc_post` / `post_factory`; Mock accepts an
   optional `write_transport` spy for hermetic tests (production mock path has none).
3. **dry-run** — `ModeController.decide` never calls `apply` when `dry_run=True`.

Guard tests: `ems/tests/test_battery_arming.py` (parametrized over `IndevoltBatteryDriver` and
`MockBatteryDriver`) plus conformance in `ems/tests/test_adapter_conformance.py`. Unarmed or
dry-run ⇒ **0** spy-transport calls. `armed` as a port member and the shutdown-path getattr
removal are owned by [#127](https://github.com/jeroenniesen/EnergyManagementSystem/issues/127)
(already landed); this slice does not reopen them.

## Adapter registry (#140)

Generic registry: `ems/sources/registry.py`. Register with
`register_adapter(domain, name, builder, metadata)`. Builders are **lazy** — registration only
stores the callable; vendor imports and network clients run when that name is chosen.

| Concern | Behaviour |
|---|---|
| Sync ports | `SolarForecastSource`, `PriceSource` — builder returns the adapter; callers use sync methods. |
| Async ports | `CarbonSource`, `HaClient` — same `register_adapter` / `build_adapter` API; the registry never awaits. |
| Unknown name | Fail-safe to a `fallback` baseline (same idea as `ems/planner/factory.py` / #70). |
| Incomplete config | Builder returns `None` → same fallback path. |
| Duplicate `(domain, name)` | Raises `ValueError`. |

**Forecast** (`ems/sources/forecast_factory.py`): `@register_forecast_provider` /
`build_solar_forecast` / `solar.forecast_provider`; the decorator delegates to
`register_adapter("forecast", …)`. Registered names: `mock`, `forecast_solar`, `solcast`.
Solcast → Forecast.Solar → model and the live gate (`meters.p1_ip`) are unchanged.
Prediction-ledger provenance still uses `type(solar_forecast).__name__`
(`ForecastSolarSource` / `SolcastSource` / `MockSolarForecastSource`) — do not rename those
classes lightly. Pre-#140 settings DBs need no migration.

**Price** (`ems/sources/price_factory.py`, [#114](https://github.com/jeroenniesen/EnergyManagementSystem/issues/114) slice a):
`@register_price_provider` / `build_price_source`; delegates to `register_adapter("price", …)`.
Registered names: `mock`, `tibber`. Live Tibber still requires `connection.use_live_prices` +
token; incomplete config fails safe to mock. Dry-run / arming rules are unchanged.

CO₂ via the registry and enum options sourced from the registry are **not** in this slice
([#113](https://github.com/jeroenniesen/EnergyManagementSystem/issues/113) slice b).

Guard tests: `ems/tests/test_adapter_registry.py`, `ems/tests/test_forecast_factory.py`,
`ems/tests/test_price_factory.py`.

## Behaviour contracts (#114 slice a)

`ems/tests/test_adapter_contracts.py` parametrizes over **every** adapter registered for
`price` and `forecast`. It checks behaviour, not only signatures:

| Port | Contract |
|---|---|
| Price | `isinstance(..., PriceSource)`; tz-aware 15-min slots; on failure `[]` (cold) or last-good |
| Forecast | `isinstance(..., SolarForecastSource)`; P10 ≤ P50 ≤ P90; network failure → labelled fallback |

Each domain has a fake/simulator in the registry (`mock`), reusing `MockPriceSource` /
`MockSolarForecastSource`. The suite is hermetic (injected transports only). Battery / meter /
CO₂ contracts are **out of scope** until #114 slices b/c.

Signature conformance for price + forecast in `ems/tests/test_adapter_conformance.py` also
reads the registry instead of a hard-coded adapter list.

## Related

- Ports: `ems/sources/ports.py` (re-exported from `ems/ports.py` and `ems/application/protocols.py`)
- Registry: `ems/sources/registry.py` (forecast + price factories)
- Composition root: `ems/connection.py::build_wiring` → `Wiring`
- Design note: `docs/superpowers/specs/2026-07-29-source-ports-design.md`
