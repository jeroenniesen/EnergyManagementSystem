"""Shared battery-topology primitives used by wiring, planning, and diagnostics."""
from __future__ import annotations

from dataclasses import dataclass


def normalize_tower_ips(master_ip: object, extra_ips: object = "") -> tuple[str, ...]:
    """Return the configured tower addresses in command order, without duplicates or blanks."""
    candidates = [master_ip, *str(extra_ips or "").split(",")]
    result: list[str] = []
    for candidate in candidates:
        address = str(candidate or "").strip()
        if address and address not in result:
            result.append(address)
    return tuple(result)


@dataclass(frozen=True)
class BatteryTopology:
    """The configured logical battery topology.

    One configured address is a single tower; additional addresses form a cluster controlled as
    one logical battery. The topology is configuration-only until the runtime probe confirms which
    addresses are reachable.
    """

    tower_ips: tuple[str, ...]

    @property
    def tower_count(self) -> int:
        return len(self.tower_ips)

    @property
    def is_configured(self) -> bool:
        return bool(self.tower_ips)

    @property
    def is_cluster(self) -> bool:
        return self.tower_count > 1

    @property
    def label(self) -> str:
        if self.tower_count == 1:
            return "battery"
        if self.tower_count > 1:
            return "battery cluster"
        return "battery"


def apply_topology_defaults(settings: dict[str, object]) -> dict[str, object]:
    """Adjust legacy cluster defaults for a single configured tower.

    Values that differ from the historical cluster defaults are treated as explicit operator
    overrides and are left untouched.
    """
    topology = BatteryTopology(normalize_tower_ips(settings.get("battery.indevolt_ip"),
                                                    settings.get("battery.indevolt_ips_extra")))
    if topology.tower_count != 1:
        return settings
    adjusted = dict(settings)
    if adjusted.get("battery.usable_kwh") == 10.8:
        adjusted["battery.usable_kwh"] = 5.4
    if adjusted.get("battery.max_charge_w") == 4000.0:
        adjusted["battery.max_charge_w"] = 2400.0
    if adjusted.get("battery.max_discharge_w") == 4000.0:
        adjusted["battery.max_discharge_w"] = 2400.0
    return adjusted
