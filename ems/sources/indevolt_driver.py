"""Indevolt write-capable battery driver — the "hands" (SPEC §6.5/§7.2).

`apply()` maps a PhysicalMode to the documented SetData writes (matched to the official Indevolt HA
integration): one POST per point, `…/Indevolt.SetData?config={"f":16,"t":<point>,"v":[<value>]}`,
where 47005=mode (1 self / 4 real-time), 47015=state (0 idle/1 charge/2 discharge), 47016=power W,
47017=target SoC.

SAFETY — this can change a real battery, so it is TRIPLE-GATED and cannot touch the user's battery
in the shipped wiring: (1) `armed=False` (read-only property — no setter), apply() refuses without
writing; (2) no write transport by default (a refusing stub); (3) the controller forces dry_run, so
ModeController.decide() never even calls apply(). Write logic is unit-tested against a mock — never
the live device. The read side reuses the read-only IndevoltReadClient.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from ems.domain import CapabilityReport, PhysicalMode
from ems.sources.indevolt import (
    K_CAPACITY,
    K_METER_CONN,
    K_MODE,
    K_STATE,
    BatteryUnavailable,
    IndevoltReadClient,
)

_log = logging.getLogger("ems.sources.indevolt_driver")

_MODE_SELF, _MODE_REALTIME = 1, 4
_STATE_CHARGING, _STATE_DISCHARGING = 1001, 1002
# SetData points (write side).
P_MODE, P_STATE, P_POWER, P_SOC = 47005, 47015, 47016, 47017
_W_IDLE, _W_CHARGE, _W_DISCHARGE = 0, 1, 2

# (point, [values]) -> response. Default refuses: no accidental path to a live write exists.
SetDataPost = Callable[[int, list[int]], object]


def _refusing_post(point: int, values: list[int]) -> object:
    raise RuntimeError(
        "Indevolt write attempted with no transport configured — refused (read-only safety)"
    )


def setdata_writes(
    mode: PhysicalMode, *, power_w: int = 2000, target_soc: int = 100
) -> list[tuple[int, list[int]]]:
    """Ordered (point, [values]) SetData writes to command `mode` (SPEC api-reference §)."""
    if mode is PhysicalMode.AUTO:
        return [(P_MODE, [_MODE_SELF])]  # vendor self-consumption (P1-zeroing)
    if mode is PhysicalMode.IDLE:
        return [(P_MODE, [_MODE_REALTIME]), (P_STATE, [_W_IDLE])]
    if mode is PhysicalMode.CHARGE:
        return [(P_MODE, [_MODE_REALTIME]), (P_STATE, [_W_CHARGE]),
                (P_POWER, [int(power_w)]), (P_SOC, [int(target_soc)])]
    if mode is PhysicalMode.DISCHARGE:
        return [(P_MODE, [_MODE_REALTIME]), (P_STATE, [_W_DISCHARGE]),
                (P_POWER, [int(power_w)]), (P_SOC, [int(target_soc)])]
    raise ValueError(f"unmapped mode {mode}")  # pragma: no cover


def mode_from_data(data: dict) -> PhysicalMode:
    """Map the read working-mode (7101) + state (6001) registers to a PhysicalMode."""
    mode, state = data.get(str(K_MODE)), data.get(str(K_STATE))
    if mode == _MODE_REALTIME:
        return {_STATE_CHARGING: PhysicalMode.CHARGE,
                _STATE_DISCHARGING: PhysicalMode.DISCHARGE}.get(state, PhysicalMode.IDLE)
    return PhysicalMode.AUTO  # self-consumption / outdoor / schedule -> vendor-managed


class IndevoltBatteryDriver:
    """BatteryDriver for the real Indevolt. Reads via IndevoltReadClient; writes via SetData ONLY
    when explicitly armed AND a real transport is injected (neither true in the default wiring)."""

    def __init__(
        self,
        ip: str,
        *,
        port: int = 8080,
        armed: bool = False,
        charge_power_w: int = 2000,
        reader: IndevoltReadClient | None = None,
        rpc_post: SetDataPost | None = None,
        timeout: float = 4.0,
    ) -> None:
        self.ip = ip
        self._armed = armed  # read-only: no setter, so it can't be flipped after construction
        self.charge_power_w = charge_power_w
        self.reader = reader or IndevoltReadClient(ip, port=port, timeout=timeout)
        self._setdata_url = f"http://{ip}:{port}/rpc/Indevolt.SetData"
        self._post = rpc_post or _refusing_post

    @property
    def armed(self) -> bool:
        return self._armed

    def probe(self) -> CapabilityReport:
        """Read-only capability probe (SPEC §6.5/M1a). Raises BatteryUnavailable if unreachable."""
        data = self.reader.read_keys([K_CAPACITY, K_MODE, K_METER_CONN])
        if not data:
            raise BatteryUnavailable("Indevolt probe: GetData empty")
        max_w = float(self.charge_power_w)
        return CapabilityReport(
            services=("charge", "discharge"),
            energy_mode_options=("self_consumption", "real_time_control"),
            has_standby=True,
            has_grid_charge_switch=True,
            p1_paired=data.get(str(K_METER_CONN)) == 1000,
            max_charge_w=max_w,
            max_discharge_w=max_w,
        )

    def current_mode(self) -> PhysicalMode:
        return mode_from_data(self.reader.read_keys([K_MODE, K_STATE]))

    def apply(self, mode: PhysicalMode) -> bool:
        """Command the battery into `mode`. Refuses (returns False, no write) unless armed.
        Returns True only if a post-write re-read confirms the mode (SPEC §6.5)."""
        if not self._armed:
            _log.warning("apply(%s) refused — driver not armed (read-only safety)", mode)
            return False
        try:
            for point, values in setdata_writes(mode, power_w=self.charge_power_w):
                self._post(point, values)
        except Exception as exc:
            _log.error("Indevolt SetData failed: %s", exc)
            return False
        try:
            return self.current_mode() is mode
        except Exception:
            return False
