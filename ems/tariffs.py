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


@dataclass(frozen=True)
class TariffPeriod:
    """Declared VAT-inclusive components for a half-open local calendar-date interval.

    When provider prices include import components, remove those before applying export
    components. Rates must come from the household contract; none are inferred by date.
    """

    start_date: str
    end_date: str
    raw_includes_import_components: bool
    import_tax_eur_per_kwh: float
    import_surcharge_eur_per_kwh: float
    export_tax_eur_per_kwh: float
    export_surcharge_eur_per_kwh: float
    export_fee_eur_per_kwh: float
    fixed_export_eur_per_kwh: float | None = None

    def __post_init__(self) -> None:
        import math
        from datetime import date

        for value in (self.start_date, self.end_date):
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError("tariff dates must use YYYY-MM-DD")
        if date.fromisoformat(self.start_date) >= date.fromisoformat(self.end_date):
            raise ValueError("tariff end_date must follow start_date")
        if type(self.raw_includes_import_components) is not bool:
            raise ValueError("raw_includes_import_components must be boolean")
        for name in self.__dataclass_fields__:
            if not name.endswith("eur_per_kwh"):
                continue
            value = getattr(self, name)
            if value is None and name == "fixed_export_eur_per_kwh":
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be finite")

    def contains(self, when, timezone: str = "Europe/Amsterdam") -> bool:
        from zoneinfo import ZoneInfo

        if when.tzinfo is None:
            raise ValueError("tariff timestamp must have a timezone")
        day = when.astimezone(ZoneInfo(timezone)).date().isoformat()
        return self.start_date <= day < self.end_date

    def normalize(self, raw_price_eur_per_kwh: float) -> TariffValue:
        import math

        raw = float(raw_price_eur_per_kwh)
        if not math.isfinite(raw):
            raise ValueError("price must be finite")
        import_components = self.import_tax_eur_per_kwh + self.import_surcharge_eur_per_kwh
        spot = raw - import_components if self.raw_includes_import_components else raw
        export = (
            spot + self.export_tax_eur_per_kwh + self.export_surcharge_eur_per_kwh
            if self.fixed_export_eur_per_kwh is None
            else self.fixed_export_eur_per_kwh
        )
        return TariffValue(raw, spot + import_components, export - self.export_fee_eur_per_kwh)


def validate_periods(periods: list[TariffPeriod]) -> list[TariffPeriod]:
    ordered = sorted(periods, key=lambda period: period.start_date)
    if any(a.end_date > b.start_date for a, b in zip(ordered, ordered[1:], strict=False)):
        raise ValueError("tariff periods must not overlap")
    return ordered
