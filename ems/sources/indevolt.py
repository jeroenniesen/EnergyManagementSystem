"""Indevolt SolidFlex OpenData — READ-ONLY client (SPEC §6.5).

Reads battery power + SoC from the local RPC (`Indevolt.GetData`). By design there is **no**
`SetData` / `charge` / `discharge` / mode write anywhere in this module — the user's battery must
never be changed. The cluster is one logical device, so we read the main tower.

Auth is HTTP Digest (user `opend` + device key) per the API reference; the key comes from the
environment (never committed). If the device returns nothing usable — the OpenData data points
aren't provisioned in the Indevolt app, or no key is supplied — `read_power_soc()` raises
`BatteryUnavailable`, so the LiveSource marks battery/soc not-fresh and the EMS falls back to AUTO
(fail-safe). Network I/O is injectable so tests never touch hardware.

NOTE: the exact register addresses for SoC/power and the GetData `config` value are device-specific
and must be confirmed against a live, provisioned device — they are configurable here for that
reason. The parsing/auth/fail-safe logic below is final and tested.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

_log = logging.getLogger("ems.sources.indevolt")

DEFAULT_PORT = 8080
DEFAULT_CONFIG = "all"
# Documented mode/state/power registers live around 47005/47015/47016 (SPEC api-reference §); the
# SoC + live-power read registers are device-specific. Overridable via the constructor / config.
DEFAULT_REGISTERS = {"soc": "47017", "power": "47016"}

RpcGet = Callable[[str], dict]  # (url) -> parsed JSON dict


class BatteryUnavailable(RuntimeError):
    """The OpenData read returned nothing usable (unprovisioned data points / missing key)."""


def _digest_get(url: str, user: str, key: str | None, timeout: float) -> dict:
    import httpx

    auth = httpx.DigestAuth(user, key) if key else None
    r = httpx.get(url, auth=auth, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _register_value(data: dict, register: str):
    """Pull a register's numeric value, tolerating either a flat {reg: value} response or a
    nested {reg: {"value": value}} shape. Returns None if absent."""
    if register not in data:
        return None
    node = data[register]
    if isinstance(node, dict):
        return node.get("value")
    return node


class IndevoltReadClient:
    """Read-only battery sense. Implements the LiveSource BatteryReader protocol
    (`read_power_soc() -> (power_w, soc_pct)`). Never writes to the device."""

    def __init__(
        self,
        ip: str,
        *,
        key: str | None = None,
        user: str = "opend",
        port: int = DEFAULT_PORT,
        config: str = DEFAULT_CONFIG,
        registers: dict[str, str] | None = None,
        timeout: float = 4.0,
        rpc_get: RpcGet | None = None,
    ) -> None:
        self.ip = ip
        self.config = config
        self.registers = registers or dict(DEFAULT_REGISTERS)
        self._url = f"http://{ip}:{port}/rpc/Indevolt.GetData"
        _user, _key, _timeout = user, key, timeout
        self._get = rpc_get or (lambda url: _digest_get(url, _user, _key, _timeout))

    def read_raw(self) -> dict:
        return self._get(f"{self._url}?config={self.config}")

    def read_power_soc(self) -> tuple[float, float]:
        """Return (battery_power_w, soc_pct). Raises BatteryUnavailable when the read is empty or
        the expected registers are absent — the caller treats that as a not-fresh signal."""
        try:
            data = self.read_raw()
        except Exception as exc:  # network / auth / transport
            raise BatteryUnavailable(f"Indevolt read failed: {type(exc).__name__}: {exc}") from exc
        if not data:
            raise BatteryUnavailable(
                "Indevolt GetData returned empty — enable the OpenData data points in the "
                "Indevolt app and supply the device key (INDEVOLT_KEY)"
            )
        soc = _register_value(data, self.registers["soc"])
        power = _register_value(data, self.registers["power"])
        if soc is None or power is None:
            missing = [n for n, v in (("soc", soc), ("power", power)) if v is None]
            raise BatteryUnavailable(
                f"Indevolt response has no usable {missing} value; keys present: {sorted(data)}"
            )
        try:
            return float(power), float(soc)
        except (TypeError, ValueError) as exc:
            # A non-numeric register (e.g. "N/A") is treated as unavailable, not a crash.
            raise BatteryUnavailable(
                f"Indevolt register value not numeric (power={power!r}, soc={soc!r}): {exc}"
            ) from exc
