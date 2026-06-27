"""Live, READ-ONLY device adapters (SPEC §6). HomeWizard energy meters via the token-less v1
local API (`GET http://<ip>/api/v1/data`), composed into a `LiveSource` that yields a
sign-normalised `RawSample`.

NOTHING in this module ever writes to a device — it is sense-only. Network I/O is injectable
(`http_get`) so unit tests never touch hardware (CLAUDE.md: no hardware in tests).

`LiveSource.read_sample()` reports WHICH signals it actually read this cycle. The recorder marks
only those fresh, so an unreachable meter — or the battery, whose read client may be absent/
unprovisioned — ages to STALE/MISSING and the data-quality gate falls back to AUTO (fail-safe).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from ems.domain import RawSample

_log = logging.getLogger("ems.sources.live")

JsonGet = Callable[[str], dict]


def _httpx_get(url: str, timeout: float) -> dict:
    import httpx

    r = httpx.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


class HomeWizardMeter:
    """A HomeWizard energy meter on the local v1 API. `read()` returns the raw /api/v1/data dict."""

    def __init__(self, ip: str, *, timeout: float = 4.0, http_get: JsonGet | None = None) -> None:
        self.ip = ip
        self.url = f"http://{ip}/api/v1/data"
        self.timeout = timeout
        # Capture the timeout by value so a later attribute change can't silently alter the
        # default getter's network behaviour mid-run.
        _timeout = timeout
        self._get = http_get or (lambda url: _httpx_get(url, _timeout))

    def read(self) -> dict:
        return self._get(self.url)


# --- sign normalisation (SPEC §4.1), pure + unit-tested against real captured payloads ---


def grid_w(p1: dict) -> float:
    """P1 net grid flow, already +import / −export — matches the domain convention directly."""
    return float(p1["active_power_w"])


def solar_w(meter: dict) -> float:
    """PV production is >= 0. A dedicated solar meter only ever has one-way flow, so the magnitude
    is the production regardless of CT orientation."""
    return abs(float(meter["active_power_w"]))


def ev_w(meter: dict) -> float:
    """EV charging is consumption (>= 0); clamp any spurious negative to 0."""
    return max(0.0, float(meter["active_power_w"]))


class BatteryReader(Protocol):
    """Read-only battery sense: instantaneous power (+discharge/−charge) and SoC %."""

    def read_power_soc(self) -> tuple[float, float]: ...


class LiveSource:
    """Compose HomeWizard meters (grid/solar/ev) + an optional read-only battery client
    (battery power + SoC). Read-only; never commands a device."""

    def __init__(
        self,
        *,
        p1: HomeWizardMeter,
        solar: HomeWizardMeter,
        car: HomeWizardMeter,
        battery: BatteryReader | None = None,
    ) -> None:
        self.p1 = p1
        self.solar = solar
        self.car = car
        self.battery = battery
        # Last good value per signal, so a single failed read reuses the prior number for load
        # reconstruction while that signal is (correctly) reported as not-fresh this cycle.
        self._last = {"grid": 0.0, "solar": 0.0, "ev": 0.0, "battery": 0.0, "soc": 0.0}

    def read_sample(self) -> tuple[RawSample, set[str]]:
        fresh: set[str] = set()

        def attempt(signal: str, read: Callable[[], float]) -> None:
            try:
                self._last[signal] = read()
                fresh.add(signal)
            except Exception as exc:
                # Keep last value; the signal ages to STALE/MISSING (fail-safe). Log it so a
                # firmware field-rename looks different from an outage (explainability §).
                _log.warning("live signal %r read failed (%s: %s); keeping last value",
                             signal, type(exc).__name__, exc)

        attempt("grid", lambda: grid_w(self.p1.read()))
        attempt("solar", lambda: solar_w(self.solar.read()))
        attempt("ev", lambda: ev_w(self.car.read()))
        if self.battery is not None:
            try:
                power_w, soc = self.battery.read_power_soc()
                self._last["battery"] = power_w
                self._last["soc"] = soc
                fresh.update({"battery", "soc"})
            except Exception as exc:
                _log.warning("battery read failed (%s: %s); battery/soc not fresh",
                             type(exc).__name__, exc)
        sample = RawSample(
            grid_power_w=self._last["grid"],
            solar_power_w=self._last["solar"],
            battery_power_w=self._last["battery"],
            ev_power_w=self._last["ev"],
            soc_pct=self._last["soc"],
        )
        return sample, fresh

    def read(self) -> RawSample:
        return self.read_sample()[0]
