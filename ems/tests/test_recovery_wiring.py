"""SPEC §8.12 / BACKLOG B-16 wiring: `_run_recovery(...)` — the observable side of missed-window
recovery. Mirrors `test_detectors_wiring.py`'s shape (a plain, directly-testable function against
canned data + the real HistoryStore/AuditStore/Notifier/CacheStore). Proves: a missed window runs
the SAME §8.11 validator and audits + notifies; a rejected catch-up is audited (not applied) and
never notified; the KV dedupe limits it to one recovery per window per day; disabled = no-op;
on-pace = no-op."""
import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.notify import Notifier
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.validator import PlanValidation
from ems.sources.prices import PriceSlot
from ems.storage.audit import AuditStore
from ems.storage.cache import CacheStore
from ems.storage.history import HistoryStore
from ems.web.api import _run_recovery

AMS = ZoneInfo("Europe/Amsterdam")
T0 = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
DEADLINE = T0 + 30 * SLOT

SIZING = dict(usable_kwh=10.0, reserve_soc_pct=10.0, max_charge_w=4000.0,
              round_trip_efficiency=0.90)


def _stores(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.init())
    audit = AuditStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(audit.init())
    cache = CacheStore(str(tmp_path / "ems.sqlite"))
    cache.init()
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    return store, audit, cache, notifier


def _notifications(store):
    return asyncio.run(store.notifications_between(
        "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))


def _audits(audit):
    return asyncio.run(audit.between("2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))


def _missed_plan():
    """A committed winter plan, priced out (no charge slots) → a MISSED window."""
    slots = [PlanSlot(T0 + i * SLOT,
                      BatteryIntent.DISCHARGE_FOR_LOAD if i in (28, 29)
                      else BatteryIntent.ALLOW_SELF_CONSUMPTION, "x")
             for i in range(32)]
    return Plan(created_at=T0, slots=tuple(slots), strategy="winter",
                target_soc=80.0, deadline=DEADLINE)


def _cheap_prices():
    return [PriceSlot(start=T0 + i * SLOT, eur_per_kwh=0.10 if 8 <= i <= 20 else 0.30)
            for i in range(32)]


def _ok_validator(plan, now):
    return PlanValidation(status="valid")


def _unsafe_validator(plan, now):
    from ems.planner.validator import Finding
    return PlanValidation(status="unsafe",
                          findings=(Finding("unsafe", "x", "cannot reach target"),))


def _run(**kw):
    base = dict(soc_pct=30.0, prices=_cheap_prices(), enabled=True, tz=AMS, margin_pp=5.0, **SIZING)
    base.update(kw)
    plan = base.pop("plan", _missed_plan())
    now = base.pop("now", T0 + 4 * SLOT)
    return asyncio.run(_run_recovery(plan, now, **base))


def test_missed_window_validates_audits_and_notifies(tmp_path):
    store, audit, cache, notifier = _stores(tmp_path)
    seen = {}

    def spy_validator(plan, now):
        seen["plan"] = plan
        return _ok_validator(plan, now)

    out = _run(cache_store=cache, notifier=notifier, audit_store=audit, validate_fn=spy_validator)

    assert out is not None and out["accepted"] is True and out["status"] == "missed"
    # The SAME validator was invoked, and on the CATCH-UP plan (which now has charge slots).
    assert "plan" in seen
    assert any(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in seen["plan"].slots)
    # Audited with the "plan recovered:" line.
    rows = _audits(audit)
    assert any(r["category"] == "plan_recovery" and "plan recovered" in r["summary"] for r in rows)
    # Notified, calmly.
    notes = _notifications(store)
    assert len(notes) == 1 and notes[0]["key"] == "plan_recovery"


def test_rejected_catch_up_is_audited_not_notified(tmp_path):
    store, audit, cache, notifier = _stores(tmp_path)
    out = _run(cache_store=cache, notifier=notifier, audit_store=audit,
               validate_fn=_unsafe_validator)
    assert out is not None and out["accepted"] is False
    rows = _audits(audit)
    assert any(r["category"] == "plan_recovery" and "rejected" in r["summary"] for r in rows)
    assert _notifications(store) == []  # never alarm on a rejected recovery


def test_kv_dedupe_one_recovery_per_window_per_day(tmp_path):
    store, audit, cache, notifier = _stores(tmp_path)
    first = _run(cache_store=cache, notifier=notifier, audit_store=audit, validate_fn=_ok_validator)
    second = _run(cache_store=cache, notifier=notifier, audit_store=audit,
                  validate_fn=_ok_validator)  # same window, same day
    assert first is not None and second is None  # deduped
    assert len(_notifications(store)) == 1
    assert sum(1 for r in _audits(audit) if r["category"] == "plan_recovery") == 1


def test_disabled_is_a_no_op(tmp_path):
    store, audit, cache, notifier = _stores(tmp_path)
    out = _run(cache_store=cache, notifier=notifier, audit_store=audit,
               validate_fn=_ok_validator, enabled=False)
    assert out is None
    assert _notifications(store) == [] and _audits(audit) == []


def test_on_pace_plan_is_a_no_op(tmp_path):
    store, audit, cache, notifier = _stores(tmp_path)
    charged = [PlanSlot(T0 + i * SLOT, BatteryIntent.GRID_CHARGE_TO_TARGET, "c",
                        target_soc=80.0, power_w=4000.0, floor_soc=10.0, deadline=DEADLINE)
               if 8 <= i < 16 else
               PlanSlot(T0 + i * SLOT, BatteryIntent.ALLOW_SELF_CONSUMPTION, "x")
               for i in range(32)]
    plan = Plan(created_at=T0, slots=tuple(charged), strategy="winter",
                target_soc=80.0, deadline=DEADLINE)
    out = _run(plan=plan, soc_pct=78.0, now=T0 + 20 * SLOT,  # within margin of target
               cache_store=cache, notifier=notifier, audit_store=audit, validate_fn=_ok_validator)
    assert out is None
    assert _notifications(store) == [] and _audits(audit) == []


def test_impossible_catch_up_still_notifies(tmp_path):
    store, audit, cache, notifier = _stores(tmp_path)
    near = T0 + 12 * SLOT  # 03:00 — barely any runway
    slots = [PlanSlot(T0 + i * SLOT, BatteryIntent.ALLOW_SELF_CONSUMPTION, "x")
             for i in range(10, 32)]
    plan = Plan(created_at=T0, slots=tuple(slots), strategy="winter", target_soc=80.0,
                deadline=near)
    out = _run(plan=plan, now=T0 + 10 * SLOT, soc_pct=30.0,
               prices=[PriceSlot(start=T0 + i * SLOT, eur_per_kwh=0.10) for i in range(32)],
               cache_store=cache, notifier=notifier, audit_store=audit, validate_fn=_ok_validator)
    assert out is not None and out["feasible"] is False
    notes = _notifications(store)
    assert len(notes) == 1 and "partial" in notes[0]["title"].lower()
