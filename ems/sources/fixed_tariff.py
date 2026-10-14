"""Fixed import-tariff price source (#117 / #118).

Produces a flat €/kWh curve as 15-minute ``PriceSlot``s. Used as the **backup** rung of the
Tibber fallback chain when live fetch fails and last-good cache is empty (#118). A flat curve
cannot create a profitable arbitrage spread, so the planner stays fail-safe; control also treats
an active fixed backup as ``prices unavailable`` (reporting/display only — Jeroen 2026-09-27).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.sources.prices import SLOT, PriceSlot

__all__ = ["FixedTariffPriceSource"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FixedTariffPriceSource:
    """Credential-free flat import tariff → 15-min slots (DST-aware local days)."""

    # Adapter status / provenance (#118): callers and quality badges key on this.
    source_label = "fixed_tariff"
    serving_fixed_backup = True  # always "backup provenance" when used as #118 fallback

    def __init__(
        self,
        eur_per_kwh: float,
        *,
        tz: ZoneInfo,
        clock: Callable[[], datetime] = _utcnow,
        horizon_slots: int | None = None,
        origin: str = "fixed_backup",
    ) -> None:
        if eur_per_kwh < 0:
            raise ValueError("fixed tariff must be >= 0")
        self.eur_per_kwh = float(eur_per_kwh)
        self.tz = tz
        self._clock = clock
        self.horizon_slots = horizon_slots
        self.origin = origin

    def slots(self) -> list[PriceSlot]:
        now_local = self._clock().astimezone(self.tz)
        midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = midnight.astimezone(UTC)
        if self.horizon_slots is None:
            end_utc = (midnight + timedelta(days=2)).astimezone(UTC)
            count = int((end_utc - start_utc) / SLOT)
        else:
            count = int(self.horizon_slots)
        rate = self.eur_per_kwh
        origin = self.origin
        return [
            PriceSlot(
                start=(start_utc + i * SLOT).astimezone(self.tz),
                eur_per_kwh=rate,
                origin=origin,
            )
            for i in range(count)
        ]
