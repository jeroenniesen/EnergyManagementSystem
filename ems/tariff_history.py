"""Append-only tariff assumptions kept in the existing settings database."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict

from ems.storage.settings import SettingsStore
from ems.tariffs import TariffPeriod, validate_periods

_APPEND_LOCK = asyncio.Lock()
_LEGACY_KEYS = {
    "export_price_model": ("prices.export_price_model", "net_metering"),
    "energy_tax_eur_per_kwh": ("prices.energy_tax_eur_per_kwh", 0.13),
    "fixed_feed_in_eur_per_kwh": ("prices.fixed_feed_in_eur_per_kwh", 0.01),
    "tibber_total_includes_all": ("grid_fees.tibber_total_includes_all", False),
    "import_fee_eur_per_kwh": ("grid_fees.import_fee_eur_per_kwh", 0.0),
    "export_fee_eur_per_kwh": ("grid_fees.export_fee_eur_per_kwh", 0.0),
}


def finance_basis(settings: dict) -> str:
    inputs = finance_tariff_kwargs(settings)
    inputs["wear"] = settings.get("planner.degradation_eur_per_kwh", .05)
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


def finance_tariff_kwargs(settings: dict) -> dict:
    legacy = {name: settings.get(key, default) for name, (key, default) in _LEGACY_KEYS.items()}
    periods = validate_periods(
        [TariffPeriod(**item) for item in settings.get("tariffs.periods", [])]
    )
    if periods:
        legacy = settings.get("tariffs.legacy", legacy)
    return {
        **legacy,
        "tariff_periods": periods,
        "legacy_before": periods[0].start_date
        if periods and "tariffs.legacy" in settings
        else None,
    }


async def append_period(store: SettingsStore, settings: dict, period: TariffPeriod) -> dict:
    async with _APPEND_LOCK:
        persisted = await store.all()
        existing = [TariffPeriod(**item) for item in persisted.get("tariffs.periods", [])]
        if existing and period.start_date < max(p.end_date for p in existing):
            raise ValueError("new tariff periods must follow existing immutable periods")
        periods = validate_periods([*existing, period])
        legacy = persisted.get("tariffs.legacy")
        if legacy is None:
            legacy = {
                name: settings.get(key, default) for name, (key, default) in _LEGACY_KEYS.items()
            }
        values = {"tariffs.periods": [asdict(p) for p in periods], "tariffs.legacy": legacy}
        await store.set_many(values)
        settings.update(values)
        return values


def economic_snapshot_at(
    settings: dict, timestamp, raw_price: float, timezone: str = "Europe/Amsterdam"
):
    """Resolve a slot's declared tariff; explicit uncovered dates have no usable valuation."""
    from zoneinfo import ZoneInfo

    from ems.economics import EconomicSnapshot
    from ems.tariffs import TariffPolicy

    if timestamp.tzinfo is None:
        raise ValueError("tariff timestamp must have a timezone")
    values = finance_tariff_kwargs(settings)
    periods = values.pop("tariff_periods")
    legacy_before = values.pop("legacy_before")
    period = next((p for p in periods if p.contains(timestamp, timezone)), None)
    common = {
        "round_trip_efficiency": float(settings.get("planner.round_trip_efficiency", 0.9)),
        "degradation_eur_per_kwh": float(settings.get("planner.degradation_eur_per_kwh", 0.05)),
        "risk_margin_eur_per_kwh": float(settings.get("planner.risk_margin_eur_per_kwh", 0.02)),
    }
    if period is not None:
        value = period.normalize(raw_price)
        return EconomicSnapshot(
            import_price_eur_per_kwh=value.import_eur_per_kwh,
            export_price_eur_per_kwh=value.export_eur_per_kwh,
            raw_price_eur_per_kwh=raw_price,
            export_model="explicit_period",
            **common,
        )
    day = timestamp.astimezone(ZoneInfo(timezone)).date().isoformat()
    if periods and not (legacy_before and day < legacy_before):
        return None
    policy = TariffPolicy(
        bool(values.get("tibber_total_includes_all", False)),
        float(values.get("import_fee_eur_per_kwh", 0)),
        float(values.get("export_fee_eur_per_kwh", 0)),
    )
    return EconomicSnapshot.from_tariff_policy(
        policy,
        raw_price_eur_per_kwh=raw_price,
        export_model=values.get("export_price_model", "net_metering"),
        energy_tax_eur_per_kwh=float(values.get("energy_tax_eur_per_kwh", 0.13)),
        fixed_feed_in_eur_per_kwh=float(values.get("fixed_feed_in_eur_per_kwh", 0.01)),
        **common,
    )
