"""Pure import/export tariff normalization for post-saldering economics."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TariffPolicy:
    """The fee policy applied to a provider's raw spot price."""

    tibber_total_includes_all: bool = False
    import_fee_eur_per_kwh: float = 0.0
    export_fee_eur_per_kwh: float = 0.0

    def normalize(self, raw_price_eur_per_kwh: float) -> TariffValue:
        raw = float(raw_price_eur_per_kwh)
        import_price = raw if self.tibber_total_includes_all else raw + self.import_fee_eur_per_kwh
        export_price = raw - self.export_fee_eur_per_kwh
        return TariffValue(raw, import_price, export_price)


@dataclass(frozen=True)
class TariffValue:
    raw_eur_per_kwh: float
    import_eur_per_kwh: float
    export_eur_per_kwh: float


def policy_from_settings(settings: dict[str, object]) -> TariffPolicy:
    """Build a defensive policy from effective settings; invalid runtime values fail safe to 0."""
    try:
        includes = bool(settings.get("grid_fees.tibber_total_includes_all", False))
        import_fee = max(0.0, float(settings.get("grid_fees.import_fee_eur_per_kwh", 0.0)))
        export_fee = max(0.0, float(settings.get("grid_fees.export_fee_eur_per_kwh", 0.0)))
    except (TypeError, ValueError):
        return TariffPolicy()
    return TariffPolicy(includes, import_fee, export_fee)


def policy_to_dict(policy: TariffPolicy) -> dict[str, object]:
    return {
        "tibber_total_includes_all": policy.tibber_total_includes_all,
        "import_fee_eur_per_kwh": policy.import_fee_eur_per_kwh,
        "export_fee_eur_per_kwh": policy.export_fee_eur_per_kwh,
        "basis": "provider total plus configured import fee; raw price minus export fee",
    }
