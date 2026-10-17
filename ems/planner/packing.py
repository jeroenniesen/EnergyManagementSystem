"""Shared charge-window packing for the rule-based planners.

The controller starts ``GRID_CHARGE`` at the earliest marked slot and runs at ``max_charge_w``,
so a cheapest-set that mixes an early medium hour with a later valley fills the early hour first.
``late_pack_charge_starts`` picks the cost-minimal contiguous window instead (tie → later).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ems.planner.schedule import SLOT
from ems.sources.prices import PriceSlot


def late_pack_charge_starts(
    pre_peak: list[PriceSlot],
    n_charge: int,
    *,
    price_of: Callable[[PriceSlot], float],
    max_buy: float,
) -> set[datetime]:
    """Pick a contiguous charge window of length ``n_charge`` that minimises cost.

    Cheapest-first slot *selection* alone is not enough: the controller starts ``GRID_CHARGE`` at
    the earliest marked slot and pumps at ``max_charge_w`` until the target, so marking an early
    medium-cheap hour plus a later deep valley still fills the early hour first. Late-pack instead
    chooses the **cost-minimal contiguous window** (tie → later window), so charging packs toward
    the deeper valley / deadline.

    Only slots with ``price_of(p) <= max_buy`` may enter a window (same buy-pool gate as before).
    If no full-length buyable contiguous window exists, falls back to the cheapest ``n_charge``
    buyable slots (honest-partial / sparse-valley cases). The returned set is at most ``n_charge``
    starts — the same size the cheapest-n slice used.
    """
    if n_charge <= 0 or not pre_peak:
        return set()
    # Sliding windows over chronological slots; require time-contiguous + buyable.
    best_starts: set[datetime] | None = None
    best_cost = float("inf")
    best_end: datetime | None = None
    limit = len(pre_peak) - n_charge + 1
    for i in range(max(0, limit)):
        window = pre_peak[i : i + n_charge]
        if len(window) < n_charge:
            break
        contiguous = all(
            window[j].start + SLOT == window[j + 1].start for j in range(n_charge - 1)
        )
        if not contiguous:
            continue
        prices = [price_of(p) for p in window]
        if any(pr > max_buy for pr in prices):
            continue
        cost = sum(prices)
        end = window[-1].start
        # Prefer lower cost; on a tie prefer the later window (pack toward the deadline).
        if cost < best_cost - 1e-12 or (
            abs(cost - best_cost) <= 1e-12 and (best_end is None or end > best_end)
        ):
            best_cost = cost
            best_starts = {p.start for p in window}
            best_end = end
    if best_starts is not None:
        return best_starts
    # Fallback: sparse buyable slots (gaps / under-charge) — cheapest-first, unchanged economics.
    pool = sorted(
        (p for p in pre_peak if price_of(p) <= max_buy),
        key=lambda p: (price_of(p), p.start),
    )
    return {p.start for p in pool[:n_charge]}
