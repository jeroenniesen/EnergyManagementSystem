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

Both I1 and I2 run in the ordinary `pytest` suite (hermetic, no network, no live Indevolt). They
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

## Related

- Ports: `ems/sources/ports.py` (re-exported from `ems/ports.py`)
- Composition root: `ems/connection.py::build_wiring` → `Wiring`
- Design note: `docs/superpowers/specs/2026-07-29-source-ports-design.md`
