from ems.domain import RawSample
from ems.sources.mock import MockSource


def test_mock_source_returns_plausible_sample():
    s = MockSource().read()
    assert isinstance(s, RawSample)
    # battery-covering steady state: house load = 200 + 0 + 800 = 1000 W
    assert s.grid_power_w + s.solar_power_w + s.battery_power_w == 1000
    assert 0 <= s.soc_pct <= 100
