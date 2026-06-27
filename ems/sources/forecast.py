"""Solar production forecast normalised to 15-minute slots, with P10/P50/P90 (SPEC §6.3).

`SolarForecastSource` is the port; `MockSolarForecastSource` synthesises a daily bell curve so
the app runs credential-free (dev/mock). Real Solcast / Forecast.Solar adapters implement the
same port. P10 < P50 < P90 (risk-aware sizing: P10 for commitments, P50 for the expected case).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

SLOT = timedelta(minutes=15)
SLOTS_PER_DAY = 96


@dataclass(frozen=True)
class ForecastSlot:
    start: datetime
    p10_w: float
    p50_w: float
    p90_w: float


class SolarForecastSource(Protocol):
    def slots(self) -> list[ForecastSlot]: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def p50_watts(local: datetime, kwp: float) -> float:
    """A daylight bell curve peaking ~13:30; zero outside ~05:00–21:00 (W)."""
    h = local.hour + local.minute / 60.0
    if h <= 5.0 or h >= 21.0:
        return 0.0
    x = (h - 13.5) / 4.0
    return max(0.0, math.exp(-x * x) * kwp * 1000.0 * 0.85)


def orientation_factor(tilt_deg: float, azimuth_deg: float) -> float:
    """Mock derate for array orientation: best at ~35° tilt facing due south (azimuth 0, per the
    SPEC raw convention where 0=South). A simple cosine falloff away from optimal, floored at 0.3
    so a badly-oriented array still produces something. Real adapters (Solcast/Forecast.Solar)
    bake orientation into their own estimate; this only shapes the credential-free mock so the
    UI controls visibly change the curve."""
    tilt_f = max(0.3, math.cos(math.radians(tilt_deg - 35.0)))
    az_f = max(0.3, math.cos(math.radians(azimuth_deg)))
    return tilt_f * az_f


class MockSolarForecastSource:
    # Opt-in marker: the API may push site.* settings (kwp/tilt/azimuth) onto this source. Real
    # adapters that configure orientation on their own side leave this False so their attributes
    # are never clobbered.
    _ems_site_configurable = True

    def __init__(
        self,
        tz: ZoneInfo,
        kwp: float = 3.0,
        clock: Callable[[], datetime] = _utcnow,
        horizon_slots: int = 2 * SLOTS_PER_DAY,
        tilt: float = 35.0,
        azimuth: float = 0.0,
    ) -> None:
        self.tz = tz
        self.kwp = kwp
        self._clock = clock
        self.horizon_slots = horizon_slots
        # Live array config (mutable): the API pushes site.* settings here so the forecast
        # responds immediately. Defaults (35°, due south) give factor 1.0 — the prior behaviour.
        self.tilt = tilt
        self.azimuth = azimuth

    def slots(self) -> list[ForecastSlot]:
        now_local = self._clock().astimezone(self.tz)
        midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        orient = orientation_factor(self.tilt, self.azimuth)
        out: list[ForecastSlot] = []
        for i in range(self.horizon_slots):
            start = (midnight + i * SLOT).astimezone(self.tz)
            p50 = p50_watts(start, self.kwp) * orient
            out.append(ForecastSlot(start=start, p10_w=0.6 * p50, p50_w=p50, p90_w=1.15 * p50))
        return out


def day_kwh_p50(slots: list[ForecastSlot], day_slots: int = SLOTS_PER_DAY) -> float:
    """Expected (P50) energy over the first `day_slots` (today), in kWh."""
    return sum(s.p50_w for s in slots[:day_slots]) * 0.25 / 1000.0
