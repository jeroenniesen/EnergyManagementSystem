"""B-27: charge_kind shares energy_flow._allocate_slot; golden vs the former api._charge_kind.

Proves behaviour-neutral dedupe: label(output) matches the legacy solar-first formula for every
charging slot on a short DST-style day (92 quarters) and a long one (100), including negative
prices. Also locks energy-story API action labels so the web contract does not drift.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.energy_flow import _allocate_slot, charge_kind
from ems.load_model import reconstruct
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import _action_from_battery, create_app

AMS = ZoneInfo("Europe/Amsterdam")


def _legacy_charge_kind(battery_w: float, solar_w: float, load_w: float) -> str:
    """Exact former body of ems.web.api._charge_kind (removed in B-27)."""
    charge = -battery_w
    solar_to_batt = min(charge, max(0.0, solar_w - load_w))
    grid_to_batt = charge - solar_to_batt
    return "grid_charge" if grid_to_batt > solar_to_batt else "solar_charge"


def _synthetic_day(n_slots: int, *, seed: int = 7) -> list[tuple[float, float, float, float]]:
    """Deterministic (battery_w, solar_w, home_w, price) tuples; includes negative prices."""
    out: list[tuple[float, float, float, float]] = []
    for i in range(n_slots):
        # Pseudo-random but stable patterns across the day.
        solar = max(0.0, 3500.0 * max(0.0, 1.0 - abs((i % 96) - 48) / 28.0) - (seed * 11 % 17) * 20)
        home = 200.0 + (i * 37 + seed * 13) % 900
        # Mix: idle, solar-charge, grid-charge, discharge.
        mode = (i + seed) % 5
        if mode == 0:
            batt = 0.0
        elif mode == 1:
            batt = -min(4000.0, max(100.0, solar - home + 50.0))  # mostly solar surplus
        elif mode == 2:
            batt = -2000.0 - (i % 7) * 100.0  # heavy charge (grid-dominated at night)
            if solar < 100:
                solar = 0.0
        elif mode == 3:
            batt = 800.0 + (i % 5) * 50.0  # discharge
        else:
            # Tie / borderline: charge ≈ solar surplus so both sources contribute.
            surplus = max(0.0, solar - home)
            batt = -(surplus + 400.0) if surplus > 0 else -500.0
        # Negative prices around midday-ish slots (soak windows).
        price = -0.05 if 40 <= (i % 96) <= 44 else (0.08 + (i % 11) * 0.02)
        out.append((batt, solar, home, price))
    return out


def test_charge_kind_matches_allocate_slot_bands():
    for batt, solar, home, _ in _synthetic_day(24, seed=3):
        if batt >= -50.0:
            continue
        bands = _allocate_slot(solar, 0.0, batt, home)
        expected = "grid_charge" if bands.grid_batt > bands.solar_batt else "solar_charge"
        assert charge_kind(batt, solar, home) == expected


def test_charge_kind_golden_vs_legacy_92_and_100_quarters_with_negative_prices():
    # DST short day ≈ 92 quarters; long day ≈ 100. Labels must match the pre-B-27 formula.
    for n in (92, 100):
        slots = _synthetic_day(n, seed=n)
        assert any(p < 0 for *_, p in slots), f"day n={n} must include negative prices"
        charging = [(b, s, h) for b, s, h, _ in slots if b < -50.0]
        assert len(charging) >= 10, f"day n={n} needs enough charging slots"
        for batt, solar, home in charging:
            assert charge_kind(batt, solar, home) == _legacy_charge_kind(batt, solar, home), (
                f"n={n} batt={batt} solar={solar} home={home}"
            )
            # API wrapper must stay wired to the shared helper.
            assert _action_from_battery(batt, solar, home) == charge_kind(batt, solar, home)


def test_energy_story_past_actions_match_charge_kind_golden(tmp_path):
    """API past-window action labels stay identical to charge_kind for seeded history."""
    import asyncio

    db = str(tmp_path / "ems.sqlite")
    # Fixed wall-clock anchors within last 24h so /api/energy-story?window=past sees them.
    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
    cases = [
        # night grid top-up
        RawSample(4400.0, 0.0, -4000.0, 0.0, 40.0),
        # sunny solar charge while house draws a little grid for itself
        RawSample(200.0, 3000.0, -1500.0, 0.0, 55.0),
        # borderline surplus (legacy equal-split tie → solar_charge)
        RawSample(500.0, 1000.0, -1000.0, 0.0, 60.0),
        # car charging: non_ev home small; charge still solar-fed after house
        RawSample(1600.0, 3500.0, -800.0, 4000.0, 55.0),
    ]

    async def seed():
        st = HistoryStore(db)
        await st.init()
        for i, raw in enumerate(cases):
            ts = (base + timedelta(minutes=15 * i)).isoformat()
            await st.record(ts, raw, reconstruct(raw))
            # Negative price on one slot — must not affect charge labelling.
            await st.upsert_price_slots([(ts, -0.02 if i == 1 else 0.15)])

    asyncio.run(seed())
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=HistoryStore(db), price_source=MockPriceSource(AMS),
        solar_forecast=MockSolarForecastSource(AMS), settings_store=SettingsStore(db),
    )
    with TestClient(app) as c:
        body = c.get("/api/energy-story?window=past").json()
    by_start = {s["start"]: s for s in body["slots"]}
    assert by_start, "expected seeded past slots"

    for i, raw in enumerate(cases):
        if raw.battery_power_w >= -50.0:
            continue
        derived = reconstruct(raw)
        want = charge_kind(raw.battery_power_w, raw.solar_power_w, derived.non_ev_load_w)
        # Locate the slot by matching powers (start may be tz-normalised).
        matches = [
            s for s in body["slots"]
            if abs(s["battery_w"] - raw.battery_power_w) < 0.5
            and abs(s["solar_w"] - raw.solar_power_w) < 0.5
        ]
        assert matches, f"missing slot for case {i}"
        assert matches[0]["action"] == want, (
            f"case {i}: API {matches[0]['action']!r} != charge_kind {want!r}"
        )
        assert want == _legacy_charge_kind(
            raw.battery_power_w, raw.solar_power_w, derived.non_ev_load_w,
        )
