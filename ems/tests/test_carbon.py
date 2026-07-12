"""Time-varying grid CO2 intensity (roadmap F3, REPORTING ONLY — never touches control/planning).
Static is the credential-free default; ElectricityMaps is the optional live adapter with strict
fail-safe degradation: live -> cached last_good -> the caller's flat fallback (see CLAUDE.md
fail-safe principle). No live HTTP — the client is injected."""
import asyncio
from datetime import UTC, datetime, timedelta

from ems.sources.carbon import ElectricityMapsCarbonSource, StaticCarbonSource


def test_static_returns_the_configured_factor():
    src = StaticCarbonSource(0.27)
    assert asyncio.run(src.current_intensity()) == 0.27


def test_electricitymaps_parses_grams_to_kilograms():
    def fake_get(url, headers):
        assert "zone=NL" in url
        assert headers["auth-token"] == "key-123"
        return {"carbonIntensity": 210.0}

    src = ElectricityMapsCarbonSource("key-123", client=fake_get)
    assert asyncio.run(src.current_intensity()) == 0.21


def test_electricitymaps_default_zone_is_nl():
    captured = {}

    def fake_get(url, headers):
        captured["url"] = url
        return {"carbonIntensity": 200.0}

    src = ElectricityMapsCarbonSource("key-123", client=fake_get)
    asyncio.run(src.current_intensity())
    assert "zone=NL" in captured["url"]


def test_electricitymaps_sanity_band_rejects_low_reading():
    src = ElectricityMapsCarbonSource("key", client=lambda url, headers: {"carbonIntensity": 49.0})
    assert asyncio.run(src.current_intensity()) is None
    assert src.last_good is None


def test_electricitymaps_sanity_band_rejects_high_reading():
    src = ElectricityMapsCarbonSource("key", client=lambda url, headers: {"carbonIntensity": 551.0})
    assert asyncio.run(src.current_intensity()) is None
    assert src.last_good is None


def test_electricitymaps_sanity_band_accepts_boundaries():
    lo = ElectricityMapsCarbonSource("key", client=lambda url, headers: {"carbonIntensity": 50.0})
    hi = ElectricityMapsCarbonSource("key", client=lambda url, headers: {"carbonIntensity": 550.0})
    assert asyncio.run(lo.current_intensity()) == 0.05
    assert asyncio.run(hi.current_intensity()) == 0.55


def test_electricitymaps_exception_with_no_prior_success_returns_none():
    def boom(url, headers):
        raise RuntimeError("down")

    src = ElectricityMapsCarbonSource("key", client=boom)
    assert asyncio.run(src.current_intensity()) is None
    assert src.last_good is None


def test_electricitymaps_exception_after_success_serves_last_good():
    # A transient outage must not blank a previously-good reading (fail-safe: never worse than
    # having no live signal at all).
    box = {"ok": True}

    def flaky(url, headers):
        if box["ok"]:
            return {"carbonIntensity": 300.0}
        raise RuntimeError("down")

    clock = {"t": datetime(2026, 7, 1, tzinfo=UTC)}
    src = ElectricityMapsCarbonSource("key", client=flaky, clock=lambda: clock["t"])
    assert asyncio.run(src.current_intensity()) == 0.30
    box["ok"] = False
    clock["t"] += timedelta(minutes=20)  # past the throttle window -> a real refetch is attempted
    assert asyncio.run(src.current_intensity()) == 0.30  # last_good served, NOT None


def test_electricitymaps_throttles_to_one_fetch_per_15_minutes():
    calls = {"n": 0}

    def fake_get(url, headers):
        calls["n"] += 1
        return {"carbonIntensity": 200.0}

    clock = {"t": datetime(2026, 7, 1, tzinfo=UTC)}
    src = ElectricityMapsCarbonSource("key", client=fake_get, clock=lambda: clock["t"])
    assert asyncio.run(src.current_intensity()) == 0.20
    clock["t"] += timedelta(minutes=5)  # still inside the 15-min window
    assert asyncio.run(src.current_intensity()) == 0.20
    assert calls["n"] == 1  # served from cache, no second fetch
    clock["t"] += timedelta(minutes=11)  # now past 15 min total since the first fetch
    assert asyncio.run(src.current_intensity()) == 0.20
    assert calls["n"] == 2
