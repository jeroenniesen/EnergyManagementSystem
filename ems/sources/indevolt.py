"""Indevolt SolidFlex OpenData — READ-ONLY client (SPEC §6.5).

Read protocol (matched to the official INDEVOLT/homeassistant-indevolt integration, which needs
only the device IP — no key, no provisioning):

    POST http://<ip>:8080/rpc/Indevolt.GetData?config={"t":[<keys>]}   (JSON, spaces stripped)
    -> {"<key>": value, ...}   (request at most 8 keys per call)

Read-only by design — there is NO SetData/charge/discharge here; the user's battery is never
changed. Network I/O is injectable so unit tests never touch hardware.

Data-point keys (SF2000 / Gen-2):
  6002 = Battery SoC (%)        6000 = Battery power (W, magnitude)
  6001 = Charge/discharge state (1000 static · 1001 charging · 1002 discharging)
  7101 = Working mode (0 outdoor · 1 self-consumption · 4 real-time · 5 schedule)
  142  = Rated capacity (kWh)   7120 = Meter connection status (1000 on · 1001 off)
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

_log = logging.getLogger("ems.sources.indevolt")

DEFAULT_PORT = 8080
K_SOC, K_POWER, K_STATE, K_MODE, K_CAPACITY, K_METER_CONN = 6002, 6000, 6001, 7101, 142, 7120
K_ROLE = 606  # Master/Slave identification (1000 master · 1001 slave · 1002 none)
_STATE_CHARGING, _STATE_DISCHARGING = 1001, 1002
_ROLE_LABEL = {1000: "master", 1001: "slave", 1002: "none"}
# Working-mode register (7101) values; the car-guard-critical distinction is "self-consumption"
# (the battery WILL discharge to cover the car) vs "standby" (it won't).
_MODE_OUTDOOR, _MODE_SELF, _MODE_REALTIME, _MODE_SCHEDULE = 0, 1, 4, 5


def tower_mode_label(mode_reg: object, state_reg: object) -> str | None:
    """Human label for a tower's actual working mode (7101) + state (6001). This is what reveals
    whether a tower really went to standby on an idle command, or is still self-consuming (and so
    discharging into the car). None when the register is missing/unrecognised."""
    try:
        m = int(mode_reg)
    except (TypeError, ValueError):
        return None
    if m == _MODE_SELF:
        return "self-consumption"
    if m == _MODE_REALTIME:
        if state_reg == _STATE_CHARGING:
            return "charging"
        if state_reg == _STATE_DISCHARGING:
            return "discharging"
        return "standby"
    return {_MODE_OUTDOOR: "outdoor", _MODE_SCHEDULE: "schedule"}.get(m)

# (keys) -> {"<key>": value}. Default does the real POST; tests inject a stub.
GetDataPost = Callable[[Iterable[int]], dict]


class BatteryUnavailable(RuntimeError):
    """The OpenData read returned nothing usable (unreachable / unexpected response)."""


def _post_getdata(url: str, keys: Iterable[int], timeout: float) -> dict:
    import httpx

    config = json.dumps({"t": list(keys)}).replace(" ", "")
    r = httpx.post(url, params={"config": config}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def signed_battery_power(power_magnitude: float, state: object) -> float:
    """Domain sign (+discharge / −charge) from the |power| value + the state register."""
    p = abs(power_magnitude)
    if state == _STATE_CHARGING:
        return -p
    if state == _STATE_DISCHARGING:
        return p
    return 0.0  # static / unknown


class IndevoltReadClient:
    """Read-only battery sense. Implements the LiveSource BatteryReader protocol
    (`read_power_soc() -> (power_w, soc_pct)`). Never writes to the device."""

    def __init__(
        self,
        ip: str,
        *,
        port: int = DEFAULT_PORT,
        timeout: float = 4.0,
        rpc_post: GetDataPost | None = None,
    ) -> None:
        self.ip = ip
        self._url = f"http://{ip}:{port}/rpc/Indevolt.GetData"
        _timeout = timeout
        self._post = rpc_post or (lambda keys: _post_getdata(self._url, keys, _timeout))

    def read_keys(self, keys: Iterable[int]) -> dict:
        return self._post(keys)

    def read_power_soc(self) -> tuple[float, float]:
        """Return (battery_power_w, soc_pct). Raises BatteryUnavailable on any failure so the
        caller treats battery/soc as not-fresh (fail-safe)."""
        try:
            data = self.read_keys([K_SOC, K_POWER, K_STATE])
        except Exception as exc:
            raise BatteryUnavailable(f"Indevolt read failed: {type(exc).__name__}: {exc}") from exc
        if not data:
            raise BatteryUnavailable("Indevolt GetData returned empty")
        soc, mag, state = data.get(str(K_SOC)), data.get(str(K_POWER)), data.get(str(K_STATE))
        if soc is None or mag is None:
            raise BatteryUnavailable(f"Indevolt response missing SoC/power; keys: {sorted(data)}")
        try:
            return signed_battery_power(float(mag), state), float(soc)
        except (TypeError, ValueError) as exc:
            raise BatteryUnavailable(f"Indevolt SoC/power not numeric: {exc}") from exc


@dataclass(frozen=True)
class TowerReading:
    """One tower's read-only state. `soc_pct`/`power_w` are None/0 when the tower is offline this
    cycle; `capacity_kwh`/`role` may be cached from an earlier read."""

    ip: str
    soc_pct: float | None
    power_w: float
    capacity_kwh: float | None
    role: str | None
    online: bool
    mode: str | None = None  # actual working mode (self-consumption / standby / charging / …)


def aggregate_soc(readings: list[TowerReading]) -> float:
    """System SoC % from per-tower readings. Capacity-weighted when every tower reports a usable
    capacity (a half-full big tower outweighs a half-full small one); plain mean otherwise.
    Readings without a SoC are ignored; raises ValueError if none has one."""
    valid = [r for r in readings if r.soc_pct is not None]
    if not valid:
        raise ValueError("aggregate_soc requires at least one reading with a SoC")
    caps = [r.capacity_kwh for r in valid]
    if all(c and c > 0 for c in caps):
        return sum(r.soc_pct * r.capacity_kwh for r in valid) / sum(caps)  # type: ignore[operator,arg-type]
    return sum(r.soc_pct for r in valid) / len(valid)  # type: ignore[misc]


class IndevoltClusterReader:
    """Read several Indevolt towers as ONE logical battery (SPEC §6.5). Aggregates SoC
    (capacity-weighted) and power (signed sum) and exposes per-tower detail. Implements the
    LiveSource BatteryReader protocol (`read_power_soc()`), so a single- or multi-tower cluster is
    wired identically. Read-only: never writes. Tolerant of a tower dropping out — it aggregates
    over whatever is reachable and only fails when NONE responds (fail-safe)."""

    # SoC/power/mode are dynamic (read every cycle); capacity/role are static (read once, cached).
    _KEYS = (K_SOC, K_POWER, K_STATE, K_MODE, K_ROLE, K_CAPACITY)

    def __init__(self, clients: list[IndevoltReadClient]) -> None:
        self._clients = list(clients)
        self._cap_cache: dict[str, float] = {}
        self._role_cache: dict[str, str] = {}

    def _read_one(self, client: IndevoltReadClient) -> TowerReading:
        try:
            data = client.read_keys(self._KEYS)
            soc = float(data[str(K_SOC)])
            power = signed_battery_power(float(data[str(K_POWER)]), data.get(str(K_STATE)))
        except Exception as exc:  # one tower down must not sink the cluster read
            _log.warning("Indevolt tower %s read failed (%s: %s)", client.ip,
                         type(exc).__name__, exc)
            # Offline this cycle: no current SoC/power (None/0 per the contract); capacity/role
            # may still be shown from an earlier read.
            return TowerReading(client.ip, None, 0.0,
                                self._cap_cache.get(client.ip),
                                self._role_cache.get(client.ip), online=False)
        cap = data.get(str(K_CAPACITY))
        if cap is not None and float(cap) > 0:
            self._cap_cache[client.ip] = float(cap)
        # The device returns the role register as a STRING ("1000"); coerce before mapping.
        role = None
        raw_role = data.get(str(K_ROLE))
        if raw_role is not None:
            try:
                role = _ROLE_LABEL.get(int(raw_role))
            except (TypeError, ValueError):
                role = None
        if role is not None:
            self._role_cache[client.ip] = role
        mode = tower_mode_label(data.get(str(K_MODE)), data.get(str(K_STATE)))
        return TowerReading(client.ip, soc, power, self._cap_cache.get(client.ip),
                            self._role_cache.get(client.ip), online=True, mode=mode)

    def read_towers(self) -> list[TowerReading]:
        return [self._read_one(c) for c in self._clients]

    def read_power_soc(self) -> tuple[float, float]:
        online = [t for t in self.read_towers() if t.online and t.soc_pct is not None]
        if not online:
            raise BatteryUnavailable("no Indevolt tower reachable")
        return sum(t.power_w for t in online), aggregate_soc(online)
