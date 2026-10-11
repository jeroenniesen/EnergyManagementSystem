"""Pure import/export tariff normalization for post-saldering economics."""
from __future__ import annotations

from dataclasses import dataclass

# Canonical settings key (#119). Legacy Tibber-named key accepted for one release.
PRICE_INCLUDES_ALL_FEES_KEY = "grid_fees.price_includes_all_fees"
LEGACY_PRICE_INCLUDES_ALL_FEES_KEY = "grid_fees.tibber_total_includes_all"


def setting_price_includes_all_fees(settings: dict[str, object]) -> bool:
    """Read the fee-includes flag; prefer the neutral key, fall back to the legacy alias."""
    if PRICE_INCLUDES_ALL_FEES_KEY in settings:
        return bool(settings[PRICE_INCLUDES_ALL_FEES_KEY])
    return bool(settings.get(LEGACY_PRICE_INCLUDES_ALL_FEES_KEY, False))


@dataclass(frozen=True)
class TariffPolicy:
    """The fee policy applied to a provider's raw spot price."""

    price_includes_all_fees: bool = False
    import_fee_eur_per_kwh: float = 0.0
    export_fee_eur_per_kwh: float = 0.0

    def normalize(self, raw_price_eur_per_kwh: float) -> TariffValue:
        raw = float(raw_price_eur_per_kwh)
        import_price = (
            raw if self.price_includes_all_fees else raw + self.import_fee_eur_per_kwh
        )
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
        includes = setting_price_includes_all_fees(settings)
        import_fee = max(0.0, float(settings.get("grid_fees.import_fee_eur_per_kwh", 0.0)))
        export_fee = max(0.0, float(settings.get("grid_fees.export_fee_eur_per_kwh", 0.0)))
    except (TypeError, ValueError):
        return TariffPolicy()
    return TariffPolicy(includes, import_fee, export_fee)


def policy_to_dict(policy: TariffPolicy) -> dict[str, object]:
    return {
        "price_includes_all_fees": policy.price_includes_all_fees,
        "import_fee_eur_per_kwh": policy.import_fee_eur_per_kwh,
        "export_fee_eur_per_kwh": policy.export_fee_eur_per_kwh,
        "basis": "provider total plus configured import fee; raw price minus export fee",
    }
