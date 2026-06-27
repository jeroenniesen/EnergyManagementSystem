"""Indevolt write-capable battery driver — the "hands" (SPEC §6.5/§7.2).

Implements the BatteryDriver port (probe/current_mode/apply). `apply()` maps a PhysicalMode to the
documented OpenData SetData registers (mode 47005 · state 47015 · power 47016 · target-SoC 47017).

SAFETY — this can change a real battery, so it is TRIPLE-GATED and cannot touch the user's battery
in the current wiring:
  1. `armed=False` by default — apply() refuses and returns False (unconfirmed) without writing.
  2. No real write transport by default — `rpc_post` defaults to a stub that raises; a live write
     requires the operator to inject a real POST transport. main.py injects none.
  3. The control system forces `dry_run`, so the ModeController never even calls apply().
The write logic is unit-tested against a mock transport; it is NEVER pointed at the live device.
The SetData wire encoding is to-confirm on a provisioned device; the register MAPPING is final.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from ems.domain import CapabilityReport, PhysicalMode
from ems.sources.indevolt import BatteryUnavailable, IndevoltReadClient

_log = logging.getLogger("ems.sources.indevolt_driver")

MODE_REG, STATE_REG, POWER_REG, SOC_REG = "47005", "47015", "47016", "47017"
_SELF_CONSUMPTION, _REALTIME = 1, 4
_STATE_IDLE, _STATE_CHARGE, _STATE_DISCHARGE = 0, 1, 2

# (url, registers) -> response. Default refuses: no accidental path to a live write exists.
RpcPost = Callable[[str, dict], object]


def _refusing_post(url: str, registers: dict) -> object:
    raise RuntimeError(
        "Indevolt write attempted with no transport configured — refused (read-only safety)"
    )


def setdata_registers(
    mode: PhysicalMode, *, power_w: int = 2000, target_soc: int = 100
) -> dict[str, int]:
    """Pure mapping PhysicalMode -> SetData register values (SPEC api-reference §)."""
    if mode is PhysicalMode.AUTO:
        return {MODE_REG: _SELF_CONSUMPTION}  # vendor self-consumption (P1-zeroing)
    if mode is PhysicalMode.IDLE:
        return {MODE_REG: _REALTIME, STATE_REG: _STATE_IDLE}
    if mode is PhysicalMode.CHARGE:
        return {MODE_REG: _REALTIME, STATE_REG: _STATE_CHARGE,
                POWER_REG: power_w, SOC_REG: target_soc}
    if mode is PhysicalMode.DISCHARGE:
        return {MODE_REG: _REALTIME, STATE_REG: _STATE_DISCHARGE,
                POWER_REG: power_w, SOC_REG: target_soc}
    raise ValueError(f"unmapped mode {mode}")  # pragma: no cover


def mode_from_registers(data: dict) -> PhysicalMode:
    """Map the read mode/state registers back to a PhysicalMode (defaults to AUTO when unclear)."""
    def _val(reg):
        node = data.get(reg)
        return node.get("value") if isinstance(node, dict) else node

    mode = _val(MODE_REG)
    if mode == _REALTIME:
        return {
            _STATE_IDLE: PhysicalMode.IDLE,
            _STATE_CHARGE: PhysicalMode.CHARGE,
            _STATE_DISCHARGE: PhysicalMode.DISCHARGE,
        }.get(_val(STATE_REG), PhysicalMode.AUTO)
    return PhysicalMode.AUTO


class IndevoltBatteryDriver:
    """BatteryDriver for the real Indevolt. Reads via IndevoltReadClient; writes via SetData ONLY
    when explicitly armed AND a real transport is injected (neither true in the default wiring)."""

    def __init__(
        self,
        ip: str,
        *,
        key: str | None = None,
        port: int = 8080,
        armed: bool = False,
        charge_power_w: int = 2000,
        reader: IndevoltReadClient | None = None,
        rpc_post: RpcPost | None = None,
        timeout: float = 4.0,
    ) -> None:
        self.ip = ip
        # Read-only: there is no setter, so nothing can flip the driver to armed after construction
        # (the safety invariant is enforced by the type, not by convention).
        self._armed = armed
        self.charge_power_w = charge_power_w
        self.reader = reader or IndevoltReadClient(ip, key=key, port=port, timeout=timeout)
        self._setdata_url = f"http://{ip}:{port}/rpc/Indevolt.SetData"
        self._post = rpc_post or _refusing_post

    @property
    def armed(self) -> bool:
        return self._armed

    def probe(self) -> CapabilityReport:
        """Read-only capability probe (SPEC §6.5/M1a). Raises BatteryUnavailable if the device
        returns nothing (OpenData not provisioned / no key)."""
        data = self.reader.read_raw()
        if not data:
            raise BatteryUnavailable("Indevolt probe: GetData empty (provision OpenData + key)")
        return CapabilityReport(
            services=("charge", "discharge"),
            energy_mode_options=("self_consumption", "real_time_control"),
            has_standby=True,
            has_grid_charge_switch=True,
            p1_paired=True,
            max_charge_w=float(self.charge_power_w),
            max_discharge_w=float(self.charge_power_w),
        )

    def current_mode(self) -> PhysicalMode:
        return mode_from_registers(self.reader.read_raw())

    def apply(self, mode: PhysicalMode) -> bool:
        """Command the battery into `mode`. Refuses (returns False, no write) unless armed.
        Returns True only if a post-write re-read confirms the mode (SPEC §6.5)."""
        if not self.armed:
            _log.warning("apply(%s) refused — driver not armed (read-only safety)", mode)
            return False
        registers = setdata_registers(mode, power_w=self.charge_power_w)
        try:
            self._post(self._setdata_url, registers)
        except Exception as exc:
            _log.error("Indevolt SetData failed: %s", exc)
            return False
        try:  # confirm by re-reading (never trust an unconfirmed write)
            return self.current_mode() is mode
        except Exception:
            return False
