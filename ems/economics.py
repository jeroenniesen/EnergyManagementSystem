"""Immutable, pure economic inputs and calculations.

This module is deliberately independent of storage and control.  It provides a single
auditable representation of the assumptions used by planner and reporting code while
leaving the existing compatibility helpers unchanged.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from ems.tariffs import TariffPolicy


@dataclass(frozen=True)
class EconomicSnapshot:
    """Normalized prices and cost allowances for one economic calculation."""

    import_price_eur_per_kwh: float
    export_price_eur_per_kwh: float
    round_trip_efficiency: float = 0.90
    degradation_eur_per_kwh: float = 0.05
    risk_margin_eur_per_kwh: float = 0.02
    export_model: str = "net_metering"
    energy_tax_eur_per_kwh: float = 0.13
    fixed_feed_in_eur_per_kwh: float = 0.01
    raw_price_eur_per_kwh: float | None = None
    import_fee_eur_per_kwh: float = 0.0
    export_fee_eur_per_kwh: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "import_price_eur_per_kwh",
            "export_price_eur_per_kwh",
            "round_trip_efficiency",
            "degradation_eur_per_kwh",
            "risk_margin_eur_per_kwh",
            "energy_tax_eur_per_kwh",
            "fixed_feed_in_eur_per_kwh",
            "import_fee_eur_per_kwh",
            "export_fee_eur_per_kwh",
        ):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite")
        if self.raw_price_eur_per_kwh is not None and not math.isfinite(self.raw_price_eur_per_kwh):
            raise ValueError("raw_price_eur_per_kwh must be finite")

    @classmethod
    def from_tariff_policy(
        cls,
        policy: TariffPolicy | None = None,
        *,
        raw_price_eur_per_kwh: float = 0.0,
        round_trip_efficiency: float = 0.90,
        degradation_eur_per_kwh: float = 0.05,
        risk_margin_eur_per_kwh: float = 0.02,
        export_model: str = "net_metering",
        energy_tax_eur_per_kwh: float = 0.13,
        fixed_feed_in_eur_per_kwh: float = 0.01,
    ) -> EconomicSnapshot:
        policy = policy or TariffPolicy()
        value = policy.normalize(raw_price_eur_per_kwh)
        return cls(
            value.import_eur_per_kwh,
            value.export_eur_per_kwh,
            round_trip_efficiency,
            degradation_eur_per_kwh,
            risk_margin_eur_per_kwh,
            export_model,
            energy_tax_eur_per_kwh,
            fixed_feed_in_eur_per_kwh,
            float(raw_price_eur_per_kwh),
            policy.import_fee_eur_per_kwh,
            policy.export_fee_eur_per_kwh,
        )

    @classmethod
    def from_settings(
        cls, settings: dict[str, object], *, raw_price_eur_per_kwh: float = 0.0, **kwargs: Any
    ) -> EconomicSnapshot:
        from ems.tariffs import policy_from_settings

        return cls.from_tariff_policy(
            policy_from_settings(settings), raw_price_eur_per_kwh=raw_price_eur_per_kwh, **kwargs
        )

    def delivered_energy_cost(self, charge_price_eur_per_kwh: float | None = None) -> float:
        """Cost per kWh delivered after round-trip losses, wear, and risk."""
        price = (
            self.import_price_eur_per_kwh
            if charge_price_eur_per_kwh is None
            else float(charge_price_eur_per_kwh)
        )
        if not math.isfinite(price):
            raise ValueError("charge price must be finite")
        efficiency = max(1e-6, min(1.0, float(self.round_trip_efficiency)))
        return price / efficiency + self.degradation_eur_per_kwh + self.risk_margin_eur_per_kwh

    def export_credit(self, price_eur_per_kwh: float | None = None) -> float:
        """Value of one exported kWh; negative values are intentional."""
        # Explicit prices are raw provider prices, matching ``finance.day_finance``.
        # The no-argument form uses the already-normalized snapshot value.
        raw = self.raw_price_eur_per_kwh if price_eur_per_kwh is None else float(price_eur_per_kwh)
        if raw is not None and not math.isfinite(raw):
            raise ValueError("export price must be finite")
        price = (
            self.export_price_eur_per_kwh
            if price_eur_per_kwh is None
            else raw - self.export_fee_eur_per_kwh
        )
        if self.export_model == "spot_minus_tax":
            return price - self.energy_tax_eur_per_kwh
        if self.export_model == "fixed":
            return self.fixed_feed_in_eur_per_kwh - self.export_fee_eur_per_kwh
        return price

    def net_benefit(
        self,
        discharge_price_eur_per_kwh: float,
        energy_kwh: float = 1.0,
        *,
        charge_price_eur_per_kwh: float | None = None,
    ) -> float:
        """Net benefit of discharging ``energy_kwh`` at an import price."""
        discharge = float(discharge_price_eur_per_kwh)
        if not math.isfinite(discharge) or not math.isfinite(float(energy_kwh)):
            raise ValueError("prices and energy must be finite")
        return (discharge - self.delivered_energy_cost(charge_price_eur_per_kwh)) * float(
            energy_kwh
        )

    def break_even_import_price(self, discharge_price_eur_per_kwh: float | None = None) -> float:
        """Maximum charge price whose delivered cost equals the discharge price."""
        target = (
            self.export_credit()
            if discharge_price_eur_per_kwh is None
            else float(discharge_price_eur_per_kwh)
        )
        if not math.isfinite(target):
            raise ValueError("discharge price must be finite")
        efficiency = max(1e-6, min(1.0, float(self.round_trip_efficiency)))
        return (target - self.degradation_eur_per_kwh - self.risk_margin_eur_per_kwh) * efficiency

    def metadata(self) -> dict[str, Any]:
        """JSON-safe assumptions suitable for reports and explanations."""
        return asdict(self)
