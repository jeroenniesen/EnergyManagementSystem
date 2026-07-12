"""Time-varying grid CO₂ intensity for Insights REPORTING ONLY (roadmap F3).

Explicitly NOT carbon-aware control/optimization: CLAUDE.md's mode-switching design and the
planner are untouched by this module — it only feeds the Insights CO₂ score (see
`ems/reporting.py`, `/api/report`). `CarbonSource` is the port; `StaticCarbonSource` is the
default, credential-free provider (the existing flat `reporting.grid_co2_factor`).
`ElectricityMapsCarbonSource` is an optional live adapter using a free personal API key from
electricitymaps.com.

NED.nl was deliberately NOT implemented: at the time of writing its public API only exposes CO₂
intensity broken down per PRODUCTION TYPE (solar/wind/gas/... shares), not a single blended
grid-intensity number — a future provider once there's a verified endpoint for that.

Fail-safe degradation (CLAUDE.md: never worse than "no EMS"): live provider → cached last-good
value → the caller's flat factor. `ElectricityMapsCarbonSource.current_intensity()` never raises —
a bad response, timeout, or an implausible reading all resolve to `last_good` (or `None` if it has
never succeeded); `ems/connection.py` falls back to `StaticCarbonSource` entirely when the signal
is misconfigured (e.g. no API key).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

_log = logging.getLogger("ems.sources.carbon")

ENDPOINT = "https://api.electricitymap.org/v3/carbon-intensity/latest"
# Sanity band (g CO2/kWh): rejects a garbled/absurd reading rather than feed it into the score.
_MIN_G_PER_KWH = 50.0
_MAX_G_PER_KWH = 550.0
# Respect the free tier: at most one live fetch per window, regardless of how often
# current_intensity() is called (once per sense cycle in practice, SPEC cycle_seconds default 300s).
_FETCH_INTERVAL = timedelta(minutes=15)
_TIMEOUT_SECONDS = 10.0


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CarbonSource(Protocol):
    async def current_intensity(self) -> float | None:
        """Current grid CO₂ intensity in kg per kWh, or None if unavailable."""
        ...


class StaticCarbonSource:
    """The default, credential-free provider: always the configured flat factor (kg CO₂/kWh)."""

    def __init__(self, factor: float) -> None:
        self.factor = factor

    async def current_intensity(self) -> float | None:
        return self.factor


# (url, headers) -> parsed JSON body. Raises on transport/HTTP error — the caller catches.
JsonGet = Callable[[str, dict], dict]


def _default_get(url: str, headers: dict, timeout: float = _TIMEOUT_SECONDS) -> dict:
    import httpx

    r = httpx.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


class ElectricityMapsCarbonSource:
    """CarbonSource backed by electricityMaps' free-tier `carbon-intensity/latest` endpoint
    (g CO₂/kWh, converted to kg/kWh here). Read-only reporting signal — never feeds the planner.

    `current_intensity()` NEVER raises: a request exception, a non-2xx response, or a reading
    outside the plausible sanity band all resolve to `last_good` (the most recent value that
    passed the sanity check), which is `None` until the first successful fetch. Throttled to at
    most one live fetch per `fetch_interval` (default 15 min) to respect the free tier; calls
    inside that window are served from `last_good` without touching the network."""

    def __init__(
        self,
        api_key: str,
        zone: str = "NL",
        *,
        client: JsonGet | None = None,
        clock: Callable[[], datetime] = _utcnow,
        fetch_interval: timedelta = _FETCH_INTERVAL,
    ) -> None:
        self.api_key = api_key
        self.zone = zone
        self._get = client or (lambda url, headers: _default_get(url, headers))
        self._clock = clock
        self._fetch_interval = fetch_interval
        self.last_good: float | None = None
        self._next_fetch_at: datetime | None = None

    async def current_intensity(self) -> float | None:
        import asyncio

        now = self._clock()
        if self._next_fetch_at is not None and now < self._next_fetch_at:
            return self.last_good
        # Mark the next allowed fetch BEFORE attempting — a failure must still back off for the
        # full window (never hammer the free tier on repeated errors).
        self._next_fetch_at = now + self._fetch_interval
        try:
            data = await asyncio.to_thread(
                self._get, f"{ENDPOINT}?zone={self.zone}", {"auth-token": self.api_key}
            )
            g_per_kwh = float(data["carbonIntensity"])
        except Exception as exc:
            _log.warning(
                "ElectricityMaps fetch failed (%s: %s); serving %s", type(exc).__name__, exc,
                "last-good" if self.last_good is not None else "nothing yet",
            )
            return self.last_good
        if not (_MIN_G_PER_KWH <= g_per_kwh <= _MAX_G_PER_KWH):
            _log.warning(
                "ElectricityMaps reading %.1f g/kWh outside the sanity band [%.0f, %.0f]; "
                "serving %s", g_per_kwh, _MIN_G_PER_KWH, _MAX_G_PER_KWH,
                "last-good" if self.last_good is not None else "nothing yet",
            )
            return self.last_good
        self.last_good = g_per_kwh / 1000.0
        return self.last_good
