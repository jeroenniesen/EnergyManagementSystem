"""Day-ahead electricity prices normalised to 15-minute slots (SPEC §6.2).

`PriceSource` is the port; `MockPriceSource` synthesises a plausible day/night curve so the
app runs credential-free (dev/mock, SPEC §11.6). A real Tibber adapter implements the same port.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

SLOT = timedelta(minutes=15)
SLOTS_PER_DAY = 96


@dataclass(frozen=True)
class PriceSlot:
    start: datetime  # tz-aware, start of the 15-min slot
    eur_per_kwh: float


class PriceSource(Protocol):
    def slots(self) -> list[PriceSlot]: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def price_for_hour(hour: int) -> float:
    """A deterministic synthetic curve: cheap overnight, morning + evening peaks (€/kWh)."""
    if 0 <= hour < 6:
        return 0.08
    if 7 <= hour < 9:
        return 0.32  # morning peak
    if 17 <= hour < 21:
        return 0.45  # evening peak
    if 9 <= hour < 17:
        return 0.18  # daytime
    return 0.12  # shoulders (06–07, 21–24)


class MockPriceSource:
    def __init__(
        self,
        tz: ZoneInfo,
        clock: Callable[[], datetime] = _utcnow,
        horizon_slots: int | None = None,  # None = two complete local days (DST-aware)
    ) -> None:
        self.tz = tz
        self._clock = clock
        self.horizon_slots = horizon_slots

    def slots(self) -> list[PriceSlot]:
        now_local = self._clock().astimezone(self.tz)
        midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = midnight.astimezone(UTC)
        if self.horizon_slots is None:
            end_utc = (midnight + timedelta(days=2)).astimezone(UTC)
            count = int((end_utc - start_utc) / SLOT)
        else:
            count = self.horizon_slots
        out: list[PriceSlot] = []
        for i in range(count):
            start = (start_utc + i * SLOT).astimezone(self.tz)
            out.append(PriceSlot(start=start, eur_per_kwh=price_for_hour(start.hour)))
        return out


def current_price(slots: list[PriceSlot], now: datetime) -> float | None:
    """The price of the slot covering `now`, or None if `now` is outside the horizon."""
    for s in slots:
        if s.start <= now < s.start + SLOT:
            return s.eur_per_kwh
    return None
