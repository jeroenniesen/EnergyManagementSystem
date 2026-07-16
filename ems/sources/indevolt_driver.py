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
import time
from collections.abc import Callable, Sequence

from ems.domain import CapabilityReport, PhysicalMode
from ems.sources.battery import BatteryWriteUnconfirmed
from ems.sources.indevolt import (
    K_CAPACITY,
    K_METER_CONN,
    K_MODE,
    K_STATE,
    BatteryUnavailable,
    DeviceQuiesce,
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
# Verified against the official Home Assistant integration (INDEVOLT/homeassistant-indevolt) AND a
# live device: each is a SEPARATE single-value write — 47005=[mode], 47015=[state], 47016=[power],
# 47017=[soc] (the docs' v=[state,power,soc] combined form does NOT work). 47005 selects working
# mode; 47015 the real-time state (it triggers the action, so write it LAST after power/soc).
P_MODE, P_STATE, P_POWER, P_SOC = 47005, 47015, 47016, 47017
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


def make_setdata_post(ip: str, port: int = 8080, timeout: float = 8.0) -> SetDataPost:
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
    mode: PhysicalMode, *, power_w: int = 2000, target_soc: int | None = None,
    max_power_w: int = _MAX_POWER_W,
) -> list[tuple[int, list[int]]]:
    """Ordered (point, [values]) SetData writes to command `mode` (SPEC api-reference §).

    `target_soc` is REQUIRED for CHARGE/DISCHARGE and has no default — a missing target is a
    programming error, never silently "charge to 100%" (energy review #3). AUTO/IDLE ignore it.
    `max_power_w` caps the power setpoint: per-device (2400 W) for a single unit, but the CLUSTER
    total (n × 2400) when commanding the master, which coordinates the whole cluster (verified
    live: the master accepts 4000 W and drives ~3.7 kW across two towers)."""
    if mode is PhysicalMode.AUTO:
        return [(P_MODE, [_MODE_SELF])]  # vendor self-consumption (P1-zeroing)
    if mode is PhysicalMode.IDLE:
        return [(P_MODE, [_MODE_REALTIME]), (P_STATE, [_W_IDLE])]  # 47015 v=0 → standby
    if mode in (PhysicalMode.CHARGE, PhysicalMode.DISCHARGE):
        if target_soc is None:
            raise ValueError(f"{mode} requires an explicit target_soc (no default-to-full)")
        state = _W_CHARGE if mode is PhysicalMode.CHARGE else _W_DISCHARGE
        power = max(_MIN_POWER_W, min(int(max_power_w), int(power_w)))
        soc = max(_MIN_SOC, min(100, int(target_soc)))
        # Separate single-value writes (the working HA form): real-time mode, then power + SoC, then
        # the state LAST (it triggers the action with power/SoC already in place).
        return [(P_MODE, [_MODE_REALTIME]), (P_POWER, [power]), (P_SOC, [soc]), (P_STATE, [state])]
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
    when explicitly armed AND a real transport is injected (neither true in the default wiring).

    CLUSTER WRITES: the command is written to EVERY tower (master + slaves), not just the master.
    An Indevolt cluster does NOT relay real-time-control from the master to its slaves — verified
    live: commanding only the master leaves the slave self-consuming, which the cluster-mismatch
    audit flagged ("1 tower NOT following the commanded charge"). Each tower is an independent
    OpenData endpoint, so we command each one. The cluster's requested power is split evenly across
    towers so the total matches what the planner sized."""

    def __init__(
        self,
        ip: str,
        *,
        port: int = 8080,
        armed: bool = False,
        charge_power_w: int = 2000,
        discharge_power_w: int | None = None,
        reader: IndevoltReadClient | None = None,
        rpc_post: SetDataPost | None = None,
        post_factory: Callable[[str], SetDataPost] | None = None,
        extra_ips: Sequence[str] = (),
        timeout: float = 4.0,
        write_attempts: int = 2,
        write_retry_backoff: float = 0.5,
        quiesce: DeviceQuiesce | None = None,
    ) -> None:
        self.ip = ip
        # Master first, then any slave towers; de-duped, blanks dropped. Reads still come from the
        # master via `reader`; WRITES fan out to every IP in this list.
        ips: list[str] = []
        for candidate in (ip, *extra_ips):
            a = (candidate or "").strip()
            if a and a not in ips:
                ips.append(a)
        self.ips = ips
        self._armed = armed  # read-only: no setter, so it can't be flipped after construction
        self.charge_power_w = int(charge_power_w)
        self.discharge_power_w = int(discharge_power_w if discharge_power_w is not None
                                     else charge_power_w)
        self.reader = reader or IndevoltReadClient(ip, port=port, timeout=timeout)
        # The device is slow under shared load (HA + app + cluster) and intermittently times out;
        # retry a write a couple times so a transient slow response doesn't false-fail it.
        self._write_attempts = max(1, int(write_attempts))
        self._write_retry_backoff = max(0.0, float(write_retry_backoff))
        # F1: while the SetData sequence (+ settle tail) is in flight, the cluster reader sharing
        # this quiesce serves cache instead of piling reads onto the saturated device. None → own
        # instance (unshared → inert; the standalone/test driver behaves exactly as before).
        self._quiesce = quiesce or DeviceQuiesce()
        # One SetData transport per tower. post_factory(ip) builds a real per-tower transport in
        # production; rpc_post (if given) is reused for ALL towers (test injection); else refusing.
        self._has_transport = post_factory is not None or rpc_post is not None
        if post_factory is not None:
            self._posts = {a: post_factory(a) for a in self.ips}
        else:
            self._posts = {a: (rpc_post or _refusing_post) for a in self.ips}

    @property
    def armed(self) -> bool:
        return self._armed

    def configure_power_limits(self, *, max_charge_w: float, max_discharge_w: float) -> None:
        self.charge_power_w = int(max_charge_w)
        self.discharge_power_w = int(max_discharge_w)

    def probe(self) -> CapabilityReport:
        """Read-only capability probe (SPEC §6.5/M1a). Raises BatteryUnavailable if unreachable."""
        data = self.reader.read_keys([K_CAPACITY, K_MODE, K_METER_CONN])
        if not data:
            raise BatteryUnavailable("Indevolt probe: GetData empty")
        return CapabilityReport(
            services=("charge", "discharge"),
            energy_mode_options=("self_consumption", "real_time_control"),
            has_standby=True,
            has_grid_charge_switch=True,
            p1_paired=data.get(str(K_METER_CONN)) == 1000,
            max_charge_w=float(self.charge_power_w),
            max_discharge_w=float(self.discharge_power_w),
        )

    def current_mode(self) -> PhysicalMode:
        return mode_from_data(self.reader.read_keys([K_MODE, K_STATE]))

    def _post_with_retry(self, post: SetDataPost, point: int, values: list[int],
                         tower_ip: str) -> object:
        """POST one SetData write, retrying on a transport error (timeout/connection) — the device
        is slow under shared load. Returns the device's response on success (a dict — caller checks
        result:true). After exhausting attempts on a transport error, raises BatteryWriteUnconfirmed
        so the caller can HOLD rather than revert (the write was likely received; the device is just
        slow)."""
        last_exc: Exception | None = None
        for attempt in range(self._write_attempts):
            try:
                return post(point, values)
            except Exception as exc:  # transport (httpx timeout/connect) — not a device rejection
                last_exc = exc
                _log.warning("Indevolt SetData %s %s=%s attempt %d/%d failed: %s",
                             tower_ip, point, values, attempt + 1, self._write_attempts, exc)
                if attempt + 1 < self._write_attempts and self._write_retry_backoff:
                    time.sleep(self._write_retry_backoff * (attempt + 1))
        raise BatteryWriteUnconfirmed(
            f"SetData to {tower_ip} {point}={values} unconfirmed after "
            f"{self._write_attempts} attempts: {last_exc}"
        ) from last_exc

    def apply(
        self, mode: PhysicalMode, *, target_soc: float | None = None,
        power_w: float | None = None,
    ) -> bool:
        """Command the battery into `mode`, charging/discharging toward `target_soc` at `power_w`.
        Refuses (returns False, no write) unless armed, AND refuses a CHARGE/DISCHARGE with no
        target_soc — it will NEVER default to charging to full (energy review #3/#4).

        CLUSTER MODEL (verified live): an Indevolt cluster is driven by the MASTER — it coordinates
        the slaves in lockstep. A real-time command (CHARGE/DISCHARGE/IDLE) is written to the MASTER
        ONLY, with the FULL cluster power (`power_w` is the cluster total, NOT split): the master
        distributes it and the slaves follow (a slave keeps REPORTING self-consumption, 7101=1,
        while it charges — that is normal, not a fault). Writing a real-time state to a slave
        directly BREAKS the master's coordination (the towers fight — one charges, one discharges).
        AUTO (return to safe self-consumption) IS written to every tower, to guarantee the whole
        cluster drops back to the vendor mode.

        Returns True when EVERY commanded tower ACCEPTED every write (each SetData returned true).
        Returns False ONLY on a genuine device REJECTION (a write returned result:false) or a
        missing transport — the controller then falls back to AUTO. RAISES BatteryWriteUnconfirmed
        when a write times out / the transport fails after retries: that is NOT a rejection (the
        device is slow under shared load and very likely received the command), so the controller
        must HOLD and re-verify next cycle rather than revert — reverting fires another write that
        also times out, leaving a half-known cluster and an ALERT spiral (the live failure mode).
        CRITICAL: the device applies a mode change with noticeable LATENCY, so we also do NOT
        re-read-and-fail here; the control loop verifies the real mode on its next cluster read and
        flags a tower that never follows (SPEC §6.5)."""
        if not self._armed:
            _log.warning("apply(%s) refused — driver not armed (read-only safety)", mode)
            return False
        if mode in (PhysicalMode.CHARGE, PhysicalMode.DISCHARGE) and target_soc is None:
            _log.warning("apply(%s) refused — no target SoC supplied (won't default to full)", mode)
            return False
        if not self._has_transport:
            _log.warning("apply(%s) refused — no write transport configured", mode)
            return False
        default_power = (
            self.discharge_power_w if mode is PhysicalMode.DISCHARGE else self.charge_power_w
        )
        total_power = int(power_w) if power_w is not None else default_power
        # The cluster total can be up to n_towers × the per-device max; the master accepts it and
        # distributes. Don't split — the master's setpoint IS the cluster figure.
        cluster_max = len(self.ips) * _MAX_POWER_W
        total_power = max(_MIN_POWER_W, min(cluster_max, total_power))
        soc = int(target_soc) if target_soc is not None else None
        # Real-time modes → MASTER only (it drives the cluster). AUTO → every tower (guarantee the
        # whole cluster returns to safe self-consumption). A transport failure on the first write
        # aborts (raises) before the rest — bounding the worst case to ~one write's retries.
        targets = self.ips if mode is PhysicalMode.AUTO else self.ips[:1]
        # F1: quiesce device READS for the duration of the actual HTTP sequence (+ a settle tail),
        # so the cluster reader sharing this quiesce doesn't flood the device's single embedded
        # server mid-write. Reads never block on this; the settle deadline is set even if a write
        # below raises/returns early (contextmanager finally), so reads can't be starved.
        with self._quiesce.writing():
            for tower_ip in targets:
                post = self._posts.get(tower_ip, _refusing_post)
                for point, values in setdata_writes(mode, power_w=total_power, target_soc=soc,
                                                    max_power_w=cluster_max):
                    resp = self._post_with_retry(post, point, values, tower_ip)
                    if not (isinstance(resp, dict) and resp.get("result")):
                        _log.error("Indevolt SetData %s %s=%s rejected: %s",
                                   tower_ip, point, values, resp)
                        return False  # genuine device rejection → caller reverts to AUTO
        return True  # accepted; the control loop confirms the mode took (latency)
