"""Static database of popular European EVs, for the (future) EV-charging advisor UI.

Data is indicative: NET/usable battery capacity and onboard AC charger limits vary by model
year, pack option, and market — treat every value here as "typical for the common variant",
not a guarantee for any specific VIN. Users can override capacity/kW per-car in settings once
that wiring lands (a later iteration; this module is intentionally standalone — no settings/api/
frontend wiring here).

Fields:
- `battery_net_kwh` is USABLE capacity (what you can actually charge/discharge), not the larger
  gross/nameplate figure manufacturers often advertise.
- `max_ac_kw` is the car's onboard AC charger limit — the ceiling a home wallbox can ever deliver
  to this car, regardless of wallbox size. It is NOT the (much higher) DC fast-charge rate.

Sources: public manufacturer spec sheets and independent EV spec aggregators, curated 2026.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CarModel:
    """One EV model/variant. `id` is a stable slug used for lookups and persistence."""

    id: str
    brand: str
    model: str
    battery_net_kwh: float
    max_ac_kw: float
    years: str


CARS: tuple[CarModel, ...] = (
    # --- Audi ---
    CarModel("audi-q4-e-tron-40", "Audi", "Q4 e-tron 40", 77.0, 11.0, "2021–present"),
    CarModel("audi-q4-e-tron-45", "Audi", "Q4 e-tron 45 quattro", 77.0, 11.0, "2021–present"),
    CarModel("audi-q8-e-tron", "Audi", "Q8 e-tron 50", 89.0, 11.0, "2023–present"),

    # --- BMW ---
    CarModel("bmw-i4-edrive40", "BMW", "i4 eDrive40", 80.7, 11.0, "2021–present"),
    CarModel("bmw-ix1-exdrive30", "BMW", "iX1 xDrive30", 64.7, 11.0, "2022–present"),
    CarModel("bmw-ix3", "BMW", "iX3", 74.0, 11.0, "2021–2024"),

    # --- BYD ---
    CarModel("byd-atto-3", "BYD", "Atto 3", 60.5, 11.0, "2022–present"),
    CarModel("byd-dolphin", "BYD", "Dolphin", 44.9, 11.0, "2023–present"),
    CarModel("byd-seal", "BYD", "Seal Design", 82.5, 11.0, "2023–present"),

    # --- Cupra ---
    CarModel("cupra-born", "Cupra", "Born 58 kWh", 55.0, 11.0, "2021–present"),
    CarModel("cupra-born-77", "Cupra", "Born 77 kWh", 77.0, 11.0, "2022–present"),

    # --- Dacia ---
    CarModel("dacia-spring", "Dacia", "Spring", 26.8, 7.4, "2021–present"),

    # --- Fiat ---
    CarModel("fiat-500e-24", "Fiat", "500e 24 kWh (11 kW option)", 21.3, 11.0, "2020–present"),
    CarModel("fiat-500e-42", "Fiat", "500e 42 kWh", 37.3, 11.0, "2020–present"),

    # --- Ford ---
    CarModel("ford-mach-e-standard", "Ford", "Mach-E Standard Range", 68.0, 11.0, "2021–present"),
    CarModel("ford-mach-e-extended", "Ford", "Mach-E Extended Range", 88.0, 11.0, "2021–present"),

    # --- Hyundai ---
    CarModel("hyundai-kona-electric", "Hyundai", "Kona Electric", 64.8, 11.0, "2021–present"),
    CarModel("hyundai-ioniq5-58", "Hyundai", "Ioniq 5 (58 kWh)", 53.0, 11.0, "2021–present"),
    CarModel("hyundai-ioniq5-77", "Hyundai", "Ioniq 5 (77 kWh)", 74.0, 11.0, "2021–present"),
    CarModel("hyundai-ioniq6", "Hyundai", "Ioniq 6 (77 kWh)", 74.0, 11.0, "2022–present"),

    # --- Kia ---
    CarModel("kia-e-niro", "Kia", "e-Niro (64 kWh)", 64.8, 7.2, "2019–present"),
    CarModel("kia-ev6-58", "Kia", "EV6 (58 kWh)", 53.0, 11.0, "2021–present"),
    CarModel("kia-ev6-77", "Kia", "EV6 (77 kWh)", 74.0, 11.0, "2021–present"),
    CarModel("kia-ev3", "Kia", "EV3 (81.4 kWh)", 78.1, 11.0, "2024–present"),
    CarModel("kia-niro-ev", "Kia", "Niro EV", 64.8, 11.0, "2022–present"),

    # --- Mercedes-Benz ---
    CarModel("mercedes-eqa-250", "Mercedes-Benz", "EQA 250", 66.5, 11.0, "2021–present"),
    CarModel("mercedes-eqb-250", "Mercedes-Benz", "EQB 250", 66.5, 11.0, "2021–present"),

    # --- MG ---
    CarModel("mg4-electric-51", "MG", "MG4 Electric (51 kWh)", 51.0, 6.6, "2022–present"),
    CarModel("mg4-electric-64", "MG", "MG4 Electric (64 kWh)", 61.7, 11.0, "2022–present"),
    CarModel("mg5-electric", "MG", "MG5 Electric", 50.3, 7.4, "2021–present"),

    # --- Mini ---
    CarModel("mini-cooper-se", "Mini", "Cooper SE", 28.9, 11.0, "2020–present"),

    # --- Nissan ---
    CarModel("nissan-leaf-40", "Nissan", "Leaf (40 kWh)", 36.0, 6.6, "2018–present"),
    CarModel("nissan-leaf-e-plus", "Nissan", "Leaf e+ (62 kWh)", 56.0, 6.6, "2019–present"),
    CarModel("nissan-ariya", "Nissan", "Ariya (63 kWh)", 63.0, 7.4, "2022–present"),

    # --- Opel ---
    CarModel("opel-corsa-e", "Opel", "Corsa-e", 45.0, 11.0, "2020–present"),
    CarModel("opel-mokka-e", "Opel", "Mokka-e", 45.0, 11.0, "2021–present"),

    # --- Peugeot ---
    CarModel("peugeot-e-208", "Peugeot", "e-208", 45.0, 11.0, "2020–present"),
    CarModel("peugeot-e-2008", "Peugeot", "e-2008", 45.0, 11.0, "2020–present"),

    # --- Polestar ---
    CarModel("polestar-2-lr-single", "Polestar", "2 LR Single Motor", 78.0, 11.0, "2020–present"),
    CarModel("polestar-2-lr-dual", "Polestar", "2 LR Dual Motor", 78.0, 11.0, "2020–present"),

    # --- Renault ---
    CarModel("renault-zoe-r110", "Renault", "Zoe R110/R135 (ZE50)", 52.0, 22.0, "2019–2024"),
    CarModel("renault-megane-e-tech", "Renault", "Megane E-Tech", 60.0, 22.0, "2022–present"),

    # --- Skoda ---
    CarModel("skoda-enyaq-60", "Skoda", "Enyaq 60", 58.0, 11.0, "2021–present"),
    CarModel("skoda-enyaq-80", "Skoda", "Enyaq 80", 77.0, 11.0, "2021–present"),

    # --- Tesla ---
    CarModel("tesla-model-3-rwd", "Tesla", "Model 3 RWD", 57.5, 11.0, "2021–present"),
    CarModel("tesla-model-3-long-range", "Tesla", "Model 3 Long Range", 75.0, 11.0, "2021–present"),
    CarModel("tesla-model-y-rwd", "Tesla", "Model Y RWD", 57.5, 11.0, "2022–present"),
    CarModel("tesla-model-y-long-range", "Tesla", "Model Y Long Range", 75.0, 11.0, "2020–present"),

    # --- Toyota ---
    CarModel("toyota-bz4x", "Toyota", "bZ4X", 64.0, 11.0, "2022–present"),

    # --- Volkswagen ---
    CarModel("vw-id3-pro", "Volkswagen", "ID.3 Pro (58 kWh)", 55.0, 11.0, "2020–present"),
    CarModel("vw-id3-pro-s", "Volkswagen", "ID.3 Pro S (77 kWh)", 75.0, 11.0, "2020–present"),
    CarModel("vw-id4-pro", "Volkswagen", "ID.4 Pro (58 kWh)", 55.0, 11.0, "2020–present"),
    CarModel("vw-id4-pro-max", "Volkswagen", "ID.4 Pro (77 kWh)", 77.0, 11.0, "2020–present"),
    CarModel("vw-id7-pro", "Volkswagen", "ID.7 Pro (77 kWh)", 77.0, 11.0, "2023–present"),

    # --- Volvo ---
    CarModel("volvo-ex30-single", "Volvo", "EX30 Single Motor", 64.0, 11.0, "2023–present"),
    CarModel("volvo-xc40-recharge", "Volvo", "XC40 Recharge", 75.0, 11.0, "2020–present"),
)


def brands() -> list[str]:
    """Sorted, de-duplicated list of brands present in `CARS`."""
    return sorted({c.brand for c in CARS})


def models_for(brand: str) -> list[CarModel]:
    """All entries for `brand`; empty list for an unknown brand."""
    return [c for c in CARS if c.brand == brand]


def by_id(car_id: str) -> CarModel | None:
    """Look up a single entry by its stable slug id; `None` when unknown."""
    for c in CARS:
        if c.id == car_id:
            return c
    return None


def to_dict(car: CarModel) -> dict:
    """Plain-dict view of a `CarModel`, e.g. for JSON serialisation."""
    return {
        "id": car.id,
        "brand": car.brand,
        "model": car.model,
        "battery_net_kwh": car.battery_net_kwh,
        "max_ac_kw": car.max_ac_kw,
        "years": car.years,
    }
