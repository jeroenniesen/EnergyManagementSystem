"""Buy-pool explainability (#315 / audit F-02, F-31).

Additive instrumentation only — does not affect charge selection.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ems.sources.prices import PriceSlot


@dataclass(frozen=True)
class CheapestSlot:
    start: datetime
    eur_per_kwh: float


@dataclass(frozen=True)
class BuyPoolInfo:
    state: str  # empty | open | not_needed | cap
    max_buy_eur_per_kwh: float
    cheapest_slot: CheapestSlot | None
    shortfall_kwh: float
    next_chance_start: datetime | None
    pool_size: int
    auto_max_buy_eur_per_kwh: float | None = None
    resident_cap_eur_per_kwh: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        cs = self.cheapest_slot
        out: dict[str, Any] = {
            "state": self.state,
            "max_buy_eur_per_kwh": round(self.max_buy_eur_per_kwh, 4),
            "cheapest_slot": (
                {
                    "start": cs.start.isoformat(),
                    "eur_per_kwh": round(cs.eur_per_kwh, 4),
                }
                if cs is not None
                else None
            ),
            "shortfall_kwh": round(self.shortfall_kwh, 3),
            "next_chance": (
                {"start": self.next_chance_start.isoformat()}
                if self.next_chance_start is not None
                else None
            ),
            "pool_size": self.pool_size,
        }
        if self.auto_max_buy_eur_per_kwh is not None:
            out["auto_max_buy_eur_per_kwh"] = round(self.auto_max_buy_eur_per_kwh, 4)
        if self.resident_cap_eur_per_kwh > 0:
            out["resident_cap_eur_per_kwh"] = round(self.resident_cap_eur_per_kwh, 4)
        return out


def classify_buy_pool_state(
    *, n_charge: int, shortfall_kwh: float, pool_size: int,
) -> str:
    """Explain the pool that already exists. ``cap`` is reserved for the daily grid-charge
    ceiling (#317) and is not inferred from a short cheap window — a partial pool is still
    ``open``. An empty horizon is not a state here: the planner leaves ``buy_pool`` unset.
    """
    if n_charge <= 0 or shortfall_kwh <= 1e-9:
        return "not_needed"
    if pool_size == 0:
        return "empty"
    return "open"


def build_buy_pool_info[P: PriceSlot](
    *,
    pre_peak: list[P],
    pool: list[P],
    n_charge: int,
    shortfall_kwh: float,
    max_buy_eur_per_kwh: float,
    price_of: Callable[[P], float],
    auto_max_buy_eur_per_kwh: float | None = None,
    resident_cap_eur_per_kwh: float = 0.0,
) -> BuyPoolInfo:
    """Instrument the pool already used for ``late_pack_charge_starts`` — no replanning."""
    pool_size = len(pool)
    state = classify_buy_pool_state(
        n_charge=n_charge, shortfall_kwh=shortfall_kwh, pool_size=pool_size,
    )
    cheapest: CheapestSlot | None = None
    if pre_peak:
        best = min(pre_peak, key=lambda p: (price_of(p), p.start))
        cheapest = CheapestSlot(start=best.start, eur_per_kwh=price_of(best))
    next_chance = min((p.start for p in pool), default=None)
    return BuyPoolInfo(
        state=state,
        max_buy_eur_per_kwh=max_buy_eur_per_kwh,
        cheapest_slot=cheapest,
        shortfall_kwh=shortfall_kwh,
        next_chance_start=next_chance,
        pool_size=pool_size,
        auto_max_buy_eur_per_kwh=auto_max_buy_eur_per_kwh,
        resident_cap_eur_per_kwh=resident_cap_eur_per_kwh,
    )


def _eur_nl(value: float) -> str:
    return f"€{value:.2f}".replace(".", ",")


def _clock_nl(when: datetime) -> str:
    return when.strftime("%H:%M")


def _resident_cap_limits_pool(info: BuyPoolInfo) -> bool:
    cap = float(info.resident_cap_eur_per_kwh)
    auto = info.auto_max_buy_eur_per_kwh
    return cap > 0 and auto is not None and cap < auto


def format_empty_buy_summary(info: BuyPoolInfo) -> str:
    """One homeowner-facing line when the buy pool is empty but energy is still needed."""
    resident = _resident_cap_limits_pool(info)
    ceiling = _eur_nl(info.resident_cap_eur_per_kwh if resident else info.max_buy_eur_per_kwh)
    ceiling_phrase = f"jouw limiet {ceiling}" if resident else f"max-inkoopprijs {ceiling}"
    if info.cheapest_slot is None:
        return (
            f"Geen koopmoment: geen prijsmoment vóór de piek ligt onder je "
            f"{ceiling_phrase}"
        )
    cs = info.cheapest_slot
    return (
        f"Geen koopmoment: goedkoopste slot {_eur_nl(cs.eur_per_kwh)} "
        f"({_clock_nl(cs.start)}) ligt boven je {ceiling_phrase}"
    )
