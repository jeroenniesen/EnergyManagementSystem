"""Shared energy economics: the two money questions the planners and finance both ask.

Post-2027 context (backlog B-05): Dutch net-metering (*saldering*) ends 1 Jan 2027. Until then,
every exported kWh nets against imports at the FULL retail price. After it ends, a dynamic contract
typically pays roughly spot MINUS energy tax for export while imports still cost the full price, or
a contract may pay a FLAT feed-in tariff. Exporting can therefore cost money on negative-spot slots
— that is real and intentional; it must not be clamped away. Pure functions, no I/O.
"""
from __future__ import annotations

EXPORT_MODELS = ("net_metering", "spot_minus_tax", "fixed")


def breakeven(
    charge_price_eur_per_kwh: float,
    *,
    round_trip_efficiency: float,
    degradation_eur_per_kwh: float,
    risk_margin_eur_per_kwh: float,
) -> float:
    """The sell price a stored kWh must beat to be worth cycling: the charge price grossed up for
    round-trip losses, plus per-kWh wear and a risk margin. Single source of truth for the
    arbitrage gate in `rule_based.py` / `adaptive.py`."""
    return (
        charge_price_eur_per_kwh / round_trip_efficiency
        + degradation_eur_per_kwh
        + risk_margin_eur_per_kwh
    )


def export_value(
    price_eur_per_kwh: float,
    *,
    model: str = "net_metering",
    energy_tax_eur_per_kwh: float = 0.13,
    fixed_feed_in_eur_per_kwh: float = 0.01,
) -> float:
    """What one exported kWh is worth under the configured feed-in model (see module docstring).

    - net_metering  → the full price (today's saldering: export nets against import).
    - spot_minus_tax → price − energy tax (post-2027 dynamic export). MAY be negative on a
      negative-spot slot — exporting can cost money — so it is deliberately NOT clamped.
    - fixed          → a flat feed-in tariff, independent of spot.

    An unknown model falls back to net_metering (fail-safe — never raise in the hot path)."""
    if model == "spot_minus_tax":
        return price_eur_per_kwh - energy_tax_eur_per_kwh
    if model == "fixed":
        return fixed_feed_in_eur_per_kwh
    return price_eur_per_kwh  # net_metering, and any unknown model (fail-safe)
