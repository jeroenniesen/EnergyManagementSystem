"""BACKLOG B-75 wiring: `_run_detectors(store, notifier, now, **gathered)` runs the pure
detectors (`ems/detectors.py`) against already-gathered plain data and fires any that trigger
through the real `Notifier` — mirrors `test_backup.py`'s pattern for `_run_backup`. Covers: one
detector raising must not block the others or propagate (fail-safe, CLAUDE.md); dedupe is proven
end-to-end through the real HistoryStore + Notifier (reusing test_notify.py's patterns); and the
store/notifier-absent no-op. Issue #128: device-unreachable dedupe + `incident_since` survive an
EMS restart via the notifications table."""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.detectors import (
    STABLE_RECOVER_AFTER,
    DeviceUnreachableState,
    open_incidents_from_notifications,
)
from ems.notify import Notifier
from ems.sources.prices import PriceSlot
from ems.storage.history import HistoryStore
from ems.web import api as api_module
from ems.web.api import _run_detectors

AMS = ZoneInfo("Europe/Amsterdam")


def _store(tmp_path) -> HistoryStore:
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.init())
    return store


def _notifications(store: HistoryStore) -> list[dict]:
    return asyncio.run(store.notifications_between(
        "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))


def _evening_now():
    from datetime import datetime
    return datetime(2026, 7, 12, 18, 0, tzinfo=AMS)


def _cheap_tomorrow_slots():
    from datetime import datetime
    tomorrow = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    slots = [PriceSlot(start=tomorrow + timedelta(minutes=15 * i), eur_per_kwh=0.20)
             for i in range(96)]
    slots[10] = PriceSlot(start=tomorrow + timedelta(minutes=150), eur_per_kwh=-0.02)
    return slots


def test_run_detectors_skips_when_store_or_notifier_is_absent(tmp_path):
    store = _store(tmp_path)
    notifier = Notifier(store, {})
    now = _evening_now()

    asyncio.run(_run_detectors(None, notifier, now, price_slots_tomorrow=_cheap_tomorrow_slots()))
    asyncio.run(_run_detectors(store, None, now, price_slots_tomorrow=_cheap_tomorrow_slots()))

    assert _notifications(store) == []


def test_run_detectors_one_failing_detector_does_not_block_others_or_raise(tmp_path, monkeypatch):
    store = _store(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})

    def boom(*a, **kw):
        raise RuntimeError("bad forecast data")

    monkeypatch.setattr(api_module, "low_solar_tomorrow", boom)

    now = _evening_now()
    asyncio.run(_run_detectors(  # must not raise despite low_solar_tomorrow blowing up
        store, notifier, now,
        p50_by_slot_tomorrow={now: 100.0},  # would be routed to the now-boobytrapped detector
        typical_daily_kwh=10.0,
        price_slots_tomorrow=_cheap_tomorrow_slots(),  # a second, healthy detector
    ))

    rows = _notifications(store)
    assert len(rows) == 1
    assert rows[0]["key"] == "price_opportunity"  # the healthy detector still fired


def test_run_detectors_dedupes_across_two_runs_same_day(tmp_path):
    store = _store(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    now = _evening_now()
    slots = _cheap_tomorrow_slots()

    asyncio.run(_run_detectors(store, notifier, now, price_slots_tomorrow=slots))
    asyncio.run(_run_detectors(store, notifier, now, price_slots_tomorrow=slots))  # same day, again

    rows = _notifications(store)
    assert len(rows) == 1  # deduped — the second run is a no-op


def test_run_detectors_fires_multiple_independent_detectors_in_one_pass(tmp_path):
    store = _store(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    now = _evening_now()
    tomorrow = now.replace(hour=0) + timedelta(days=1)

    asyncio.run(_run_detectors(
        store, notifier, now,
        p50_by_slot_tomorrow={tomorrow.replace(hour=12): 400.0},  # tiny vs. typical -> grey day
        typical_daily_kwh=10.0,
        price_slots_tomorrow=_cheap_tomorrow_slots(),
        projected_soc_at_peak=20.0, needed_soc=50.0, confidence_level="high",
    ))

    keys = {r["key"] for r in _notifications(store)}
    assert keys == {"low_solar_tomorrow", "price_opportunity", "peak_risk"}


# ---------------------------------------------------------------------------------------------
# device_unreachable wiring (issue #128) — real HistoryStore, restart survival
# ---------------------------------------------------------------------------------------------

def test_device_unreachable_dedupe_and_incident_since_survive_restart(tmp_path):
    """Down push lands in HistoryStore; a fresh in-memory state rebuilt from notifications keeps
    the same incident_since so a second cycle after "restart" dedupes instead of re-pushing."""
    store = _store(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    last = datetime(2026, 7, 12, 10, 0, tzinfo=AMS)
    now_down = last + timedelta(minutes=15)
    states: dict[str, DeviceUnreachableState] = {}

    asyncio.run(_run_detectors(
        store, notifier, now_down,
        device_states=states,
        battery_last_fresh_at=last,
        battery_stand="zelfverbruik",
        battery_stand_confirmed=True,
        dry_run=True,
    ))
    rows = _notifications(store)
    assert len(rows) == 1
    assert rows[0]["key"] == "battery_down"
    incident_iso = states["battery"].incident_since.isoformat()
    assert rows[0]["dedupe_key"] == f"battery_down:{incident_iso}"

    # Simulate EMS restart: discard in-memory state, rebuild from the notifications table.
    restored = open_incidents_from_notifications(rows)
    assert restored["battery"].incident_since.isoformat() == incident_iso
    assert restored["battery"].down_push_sent is True

    # Same outage still ongoing — second cycle must NOT insert another down row (dedupe).
    asyncio.run(_run_detectors(
        store, notifier, now_down + timedelta(minutes=5),
        device_states=restored,
        battery_last_fresh_at=last,
        battery_stand="zelfverbruik",
        dry_run=True,
    ))
    assert len(_notifications(store)) == 1

    # After 10 min stable contact, one recovery push; incident clears.
    t_contact = now_down + timedelta(minutes=6)
    asyncio.run(_run_detectors(
        store, notifier, t_contact,
        device_states=restored,
        battery_last_fresh_at=t_contact,
        dry_run=True,
    ))
    asyncio.run(_run_detectors(
        store, notifier, t_contact + STABLE_RECOVER_AFTER,
        device_states=restored,
        battery_last_fresh_at=t_contact + STABLE_RECOVER_AFTER,
        dry_run=True,
    ))
    keys = [r["key"] for r in _notifications(store)]
    assert keys == ["battery_down", "battery_up"]
    assert _notifications(store)[1]["body"] == "Batterij weer bereikbaar, EMS kijkt weer mee."
    assert restored["battery"].incident_since is None

    # After recovery, a fresh restart sees no open incident.
    assert open_incidents_from_notifications(_notifications(store)) == {}
