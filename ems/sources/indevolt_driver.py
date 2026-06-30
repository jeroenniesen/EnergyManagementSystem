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

# NOTE: the device uses DIFFERENT encodings for reads vs writes — never mix them.
#   READ side  (GetData): mode 7101 ∈ {1 self, 4 real-time}; state 6001 ∈ {1000 static, 1001
#                         charging, 1002 discharging}  →  _MODE_*/_STATE_* below + mode_from_data().
#   WRITE side (SetData): state value v ∈ {0 idle, 1 charge, 2 discharge}  →  _W_* below.
_MODE_SELF, _MODE_REALTIME = 1, 4  # read-side working-mode (7101) values
_STATE_CHARGING, _STATE_DISCHARGING = 1001, 1002  # read-side state (6001) values
# SetData points (write side) + their state values (NOT comparable to _STATE_* read values).
# Per docs.indevolt.com OpenData: a charge/discharge command is ONE multi-register write starting at
# 47015 — v=[state, power, soc] (function 16 writes 47015/47016/47017 together). 47005 selects the
# working mode. Writing state/power/soc as SEPARATE calls does NOT reliably start charging.
P_MODE, P_STATE = 47005, 47015
_W_IDLE, _W_CHARGE, _W_DISCHARGE = 0, 1, 2
# Device limits (SolidFlex/PowerFlex per OpenData docs): power 50–2400 W, target SoC 5–100 %. Out-of
# -range values are rejected by the device — clamp so a plan asking for 4 kW doesn't silently fail.
_MIN_POWER_W, _MAX_POWER_W, _MIN_SOC = 50, 2400, 5

# (point, [values]) -> response. Default refuses: no accidental path to a live write exists.
SetDataPost = Callable[[int, list[int]], object]


def _refusing_post(point: int, values: list[int]) -> object:
    raise RuntimeError(
        "Indevolt write attempted with no transport configured — refused (read-only safety)"
    )


def make_setdata_post(ip: str, port: int = 8080, timeout: float = 4.0) -> SetDataPost:
    """Build a REAL SetData write transport (point, [values]) -> response, matching the official
    integration: POST /rpc/Indevolt.SetData?config={"f":16,"t":<point>,"v":[<values>]}.

    Returned ONLY when the operator explicitly enables operational mode; an unarmed driver never
    calls it. This is the single place the EMS can change the battery."""
    import json as _json

    url = f"http://{ip}:{port}/rpc/Indevolt.SetData"

    def post(point: int, values: list[int]) -> object:
        import httpx

        config = _json.dumps({"f": 16, "t": point, "v": list(values)}).replace(" ", "")
        r = httpx.post(url, params={"config": config}, timeout=timeout)
        r.raise_for_status()
        return r.json()

    return post


def setdata_writes(
    mode: PhysicalMode, *, power_w: int = 2000, target_soc: int | None = None
) -> list[tuple[int, list[int]]]:
    """Ordered (point, [values]) SetData writes to command `mode` (SPEC api-reference §).

    `target_soc` is REQUIRED for CHARGE/DISCHARGE and has no default — a missing target is a
    programming error, never silently "charge to 100%" (energy review #3). AUTO/IDLE ignore it."""
    if mode is PhysicalMode.AUTO:
        return [(P_MODE, [_MODE_SELF])]  # vendor self-consumption (P1-zeroing)
    if mode is PhysicalMode.IDLE:
        return [(P_MODE, [_MODE_REALTIME]), (P_STATE, [_W_IDLE])]  # 47015 v=0 → standby
    if mode in (PhysicalMode.CHARGE, PhysicalMode.DISCHARGE):
        if target_soc is None:
            raise ValueError(f"{mode} requires an explicit target_soc (no default-to-full)")
        state = _W_CHARGE if mode is PhysicalMode.CHARGE else _W_DISCHARGE
        power = max(_MIN_POWER_W, min(_MAX_POWER_W, int(power_w)))
        soc = max(_MIN_SOC, min(100, int(target_soc)))
        # state + power + SoC written TOGETHER at 47015 (v=[state, power, soc]) — the documented
        # single-call form; the device acts on this and reads power/SoC from the same write.
        return [(P_MODE, [_MODE_REALTIME]), (P_STATE, [state, power, soc])]
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

    def apply(
        self, mode: PhysicalMode, *, target_soc: float | None = None,
        power_w: float | None = None,
    ) -> bool:
        """Command the battery into `mode`, charging/discharging toward `target_soc` at `power_w`.
        Refuses (returns False, no write) unless armed, AND refuses a CHARGE/DISCHARGE with no
        target_soc — it will NEVER default to charging to full (energy review #3/#4). Returns True
        only if a post-write re-read confirms the mode (SPEC §6.5)."""
        if not self._armed:
            _log.warning("apply(%s) refused — driver not armed (read-only safety)", mode)
            return False
        if mode in (PhysicalMode.CHARGE, PhysicalMode.DISCHARGE) and target_soc is None:
            _log.warning("apply(%s) refused — no target SoC supplied (won't default to full)", mode)
            return False
        power = int(power_w) if power_w is not None else self.charge_power_w
        soc = int(target_soc) if target_soc is not None else None
        try:
            for point, values in setdata_writes(mode, power_w=power, target_soc=soc):
                self._post(point, values)
        except Exception as exc:
            _log.error("Indevolt SetData failed: %s", exc)
            return False
        try:
            return self.current_mode() is mode
        except Exception:
            return False
