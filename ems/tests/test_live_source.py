import asyncio

from ems.sources.live import HomeWizardMeter, LiveSource, ev_w, grid_w, solar_w

# Real payloads captured from the user's devices on 2026-06-28 ~01:15 local (night).
P1 = {"active_power_w": 3, "active_power_l1_w": -348, "total_power_import_kwh": 50664.413,
      "total_power_export_kwh": 11388.854}
SOLAR = {"active_power_w": 0, "active_power_l1_w": 0, "total_power_export_kwh": 6384.076}
CAR = {"active_power_w": 0, "active_power_l1_w": 0, "total_power_import_kwh": 9445.629}


def test_grid_is_net_flow_signed():
    assert grid_w(P1) == 3.0  # +import / -export, used directly
    assert grid_w({"active_power_w": -800}) == -800.0  # exporting


def test_solar_is_production_negated_and_clamped():
    # This meter registers production as export (negative active_power_w); production = -x, >=0.
    assert solar_w(SOLAR) == 0.0  # night / idle
    assert solar_w({"active_power_w": -2400}) == 2400.0  # producing 2.4 kW
    # A small POSITIVE reading is an inverter night-draw, NOT production -> clamp to 0 (no fake PV).
    assert solar_w({"active_power_w": 12}) == 0.0


def test_ev_is_nonnegative_consumption():
    assert ev_w(CAR) == 0.0
    assert ev_w({"active_power_w": 7200}) == 7200.0  # charging
    assert ev_w({"active_power_w": -5}) == 0.0  # clamp spurious negative


def test_meter_read_uses_injected_getter():
    seen = {}

    def fake_get(url):
        seen["url"] = url
        return P1

    m = HomeWizardMeter("192.168.50.92", http_get=fake_get)
    assert m.read()["active_power_w"] == 3
    assert seen["url"] == "http://192.168.50.92/api/v1/data"


def _meter(payload):
    return HomeWizardMeter("x", http_get=lambda _u: payload)


def test_live_source_composes_meters_into_sample():
    src = LiveSource(p1=_meter(P1), solar=_meter(SOLAR), car=_meter(CAR))
    sample, fresh = src.read_sample()
    assert sample.grid_power_w == 3.0
    assert sample.solar_power_w == 0.0
    assert sample.ev_power_w == 0.0
    assert fresh == {"grid", "solar", "ev"}  # no battery client -> battery/soc not fresh
    assert "battery" not in fresh and "soc" not in fresh


def test_failed_meter_keeps_last_value_and_drops_from_fresh():
    boom_calls = {"n": 0}

    def flaky(_url):
        boom_calls["n"] += 1
        if boom_calls["n"] == 1:
            return {"active_power_w": 1500}  # first read OK
        raise OSError("unreachable")  # later reads fail

    p1 = HomeWizardMeter("x", http_get=flaky)
    src = LiveSource(p1=p1, solar=_meter(SOLAR), car=_meter(CAR))
    s1, f1 = src.read_sample()
    assert s1.grid_power_w == 1500.0 and "grid" in f1
    s2, f2 = src.read_sample()
    assert s2.grid_power_w == 1500.0  # last good value retained
    assert "grid" not in f2  # but reported as not fresh -> ages to stale


class _FakeBattery:
    def read_power_soc(self):
        return -1200.0, 64.0  # charging at 1.2 kW, 64% SoC


def test_battery_client_supplies_battery_and_soc():
    src = LiveSource(p1=_meter(P1), solar=_meter(SOLAR), car=_meter(CAR), battery=_FakeBattery())
    sample, fresh = src.read_sample()
    assert sample.battery_power_w == -1200.0
    assert sample.soc_pct == 64.0
    assert {"battery", "soc"} <= fresh


class _UnavailableBattery:
    def read_power_soc(self):
        raise RuntimeError("OpenData not provisioned")


def test_unavailable_battery_leaves_signals_not_fresh():
    src = LiveSource(p1=_meter(P1), solar=_meter(SOLAR), car=_meter(CAR),
                     battery=_UnavailableBattery())
    _sample, fresh = src.read_sample()
    assert "battery" not in fresh and "soc" not in fresh
    assert fresh == {"grid", "solar", "ev"}


def test_recorder_marks_only_fresh_signals(tmp_path):
    from ems.freshness import FreshnessTracker
    from ems.sense import SIGNALS, Recorder
    from ems.storage.history import HistoryStore

    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    src = LiveSource(p1=_meter(P1), solar=_meter(SOLAR), car=_meter(CAR))  # no battery

    async def run():
        await store.init()
        rec = Recorder(src, store, fr)
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        await rec.sense_once(now)
        return fr.snapshot(now)

    snap = asyncio.run(run())
    assert snap["grid"] == "fresh" and snap["solar"] == "fresh" and snap["ev"] == "fresh"
    assert snap["battery"] == "missing" and snap["soc"] == "missing"  # never read -> fail-safe
