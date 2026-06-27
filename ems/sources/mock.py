"""Deterministic synthetic source for dev/mock mode (SPEC §11.6) — no HA/battery needed."""
from __future__ import annotations

from ems.domain import RawSample


class MockSource:
    def read(self) -> RawSample:
        # Battery-covering steady state: 1000 W house load, solar off, mid SoC.
        return RawSample(
            grid_power_w=200.0,
            solar_power_w=0.0,
            battery_power_w=800.0,
            ev_power_w=0.0,
            soc_pct=55.0,
        )
