"""Live solar forecast via Forecast.Solar (keyless public API, SPEC §6.3).

`GET https://api.forecast.solar/estimate/{lat}/{lon}/{tilt}/{azimuth}/{kwp}` returns
`result.watts` (timestamp → expected W) for today+tomorrow. Azimuth convention matches ours
(0 = south, −90 = east, +90 = west). The free tier is rate-limited (~12 calls/h per IP), so the
result is **cached** (default 30 min) and the dashboard's frequent polling reuses it. On any error
or rate-limit it **falls back** to the built-in model curve, and `source_label` reports which is in
use so the UI can say "live" vs "estimated". Network I/O is injectable for tests.

The keyless API gives a single expected estimate; P10/P90 are derived bands around it (P10 = 0.6×,
P90 = 1.15×) — same risk shape as the model, honestly labelled as derived.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.sources.forecast import SLOT, SLOTS_PER_DAY, ForecastSlot, MockSolarForecastSource

_log = logging.getLogger("ems.sources.forecast_solar")

JsonGet = Callable[[str], dict]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _httpx_get(url: str, timeout: float) -> dict:
    import httpx

    r = httpx.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def parse_watts(data: dict, tz: ZoneInfo, midnight: datetime, n_slots: int) -> list[ForecastSlot]:
    """Resample Forecast.Solar `result.watts` (sparse local timestamps) onto a 15-min grid from
    `midnight`. Stepwise hold of the most recent sample; 0 outside the sampled range."""
    watts = ((data or {}).get("result") or {}).get("watts") or {}
    points: list[tuple[datetime, float]] = []
    for ts, w in watts.items():
        try:
            dt = datetime.fromisoformat(ts)
            # Forecast.Solar returns bare local wall-clock strings; localise. If a future response
            # ever embeds an offset, convert it (don't silently re-label, which shifts the moment).
            dt = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
            points.append((dt, float(w)))
        except (ValueError, TypeError):
            continue
    points.sort(key=lambda p: p[0])
    out: list[ForecastSlot] = []
    j = 0
    held = 0.0  # latest sample at/just before the slot; 0 before sunrise (and ~0 after sunset)
    for i in range(n_slots):
        start = midnight + i * SLOT
        while j < len(points) and points[j][0] <= start:
            held = points[j][1]
            j += 1
        out.append(ForecastSlot(start=start, p10_w=0.6 * held, p50_w=held, p90_w=1.15 * held))
    return out


class ForecastSolarSource:
    """SolarForecastSource backed by Forecast.Solar, cached + with a model fallback."""

    def __init__(
        self,
        *,
        tz: ZoneInfo,
        lat: float,
        lon: float,
        tilt: float,
        azimuth: float,
        kwp: float,
        ttl_seconds: float = 1800.0,
        horizon_slots: int = 2 * SLOTS_PER_DAY,
        http_get: JsonGet | None = None,
        clock: Callable[[], datetime] = _utcnow,
        fallback: object | None = None,
    ) -> None:
        self.tz = tz
        self.lat, self.lon, self.tilt, self.azimuth, self.kwp = lat, lon, tilt, azimuth, kwp
        self.ttl_seconds = ttl_seconds
        self.horizon_slots = horizon_slots
        self._clock = clock
        self._timeout = 12.0
        self._get = http_get or (lambda url: _httpx_get(url, self._timeout))
        self._fallback = fallback or MockSolarForecastSource(
            tz, kwp=kwp, clock=clock, horizon_slots=horizon_slots
        )
        self._cache: tuple[datetime, list[ForecastSlot]] | None = None
        self.source_label = "forecast.solar"

    @property
    def url(self) -> str:
        return (
            f"https://api.forecast.solar/estimate/"
            f"{self.lat}/{self.lon}/{self.tilt:g}/{self.azimuth:g}/{self.kwp:g}"
        )

    def slots(self) -> list[ForecastSlot]:
        now = self._clock()
        if self._cache is not None and (now - self._cache[0]).total_seconds() < self.ttl_seconds:
            return self._cache[1]
        local = now.astimezone(self.tz)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            data = self._get(self.url)
            # Fall back only when the response is truly empty — NOT when production is honestly
            # zero (winter/high-latitude/commissioning), which is a valid live answer.
            if not (((data or {}).get("result") or {}).get("watts") or {}):
                raise ValueError("Forecast.Solar returned empty watts")
            slots = parse_watts(data, self.tz, midnight, self.horizon_slots)
            self.source_label = "forecast.solar"
        except Exception as exc:
            _log.warning("Forecast.Solar fetch failed (%s: %s); using model fallback",
                         type(exc).__name__, exc)
            slots = self._fallback.slots()
            self.source_label = "model (fallback)"
        self._cache = (now, slots)
        return slots
