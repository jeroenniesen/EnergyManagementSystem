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

_log = logging.getLogger("ems.sources.indevolt")

DEFAULT_PORT = 8080
K_SOC, K_POWER, K_STATE, K_MODE, K_CAPACITY, K_METER_CONN = 6002, 6000, 6001, 7101, 142, 7120
_STATE_CHARGING, _STATE_DISCHARGING = 1001, 1002

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
