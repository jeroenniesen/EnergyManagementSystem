"""Deterministic synthetic source for dev/mock mode (SPEC §11.6) — no HA/battery needed."""
from __future__ import annotations

from ems.domain import RawSample


class MockSource:
    def __init__(self, *, total_gas_m3: float | None = None) -> None:
        # Optional: tests that want a gas reading in the mock sample pass a value; real dev/mock
        # runs default to None (no gas meter), matching a household without one.
        self.total_gas_m3 = total_gas_m3

    def read(self) -> RawSample:
        # Battery-covering steady state: 1000 W house load, solar off, mid SoC.
        return RawSample(
            grid_power_w=200.0,
            solar_power_w=0.0,
            battery_power_w=800.0,
            ev_power_w=0.0,
            soc_pct=55.0,
            total_gas_m3=self.total_gas_m3,
        )
