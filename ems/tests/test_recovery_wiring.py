"""SPEC §8.12 / BACKLOG B-16 wiring: `_run_recovery(...)` — the observable side of missed-window
recovery. Mirrors `test_detectors_wiring.py`'s shape (a plain, directly-testable function against
canned data + the real HistoryStore/AuditStore/Notifier/CacheStore). Proves: a missed window runs
the SAME §8.11 validator and audits + notifies; a rejected catch-up is audited (not applied) and
never notified; the KV dedupe limits it to one recovery per local day per root cause (#316 / F-50;
the deadline is not the key); disabled = no-op; on-pace = no-op."""
import asyncio
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.notify import Notifier
from ems.planner.recovery import CatchUpResult, CompletionStatus
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
    """Same local day and the same (unclassified) catch-up still collapses to one row.

    #316 moved the key off the deadline onto (local day, root cause). This case buys slots
    and matches none of the four blocker causes, so it shares the per-day `recovered` bucket.
    """
    store, audit, cache, notifier = _stores(tmp_path)
    first = _run(cache_store=cache, notifier=notifier, audit_store=audit, validate_fn=_ok_validator)
    second = _run(cache_store=cache, notifier=notifier, audit_store=audit,
                  validate_fn=_ok_validator)  # same day, deadline unchanged
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


def _priced_out_prices():
    """Discharge peak cheap, everything else above the recovery buy ceiling → slots_used 0."""
    return [PriceSlot(start=T0 + i * SLOT, eur_per_kwh=0.01 if i in (28, 29) else 0.80)
            for i in range(32)]


def _recovery_rows(audit):
    return [r for r in _audits(audit) if r["category"] == "plan_recovery"]


def test_zero_open_buy_slots_names_cause_without_a_target_percentage(tmp_path):
    """#316 AC2: empty pool copy names the cause and carries no target percentage."""
    store, audit, cache, notifier = _stores(tmp_path)
    out = _run(prices=_priced_out_prices(), cache_store=cache, notifier=notifier,
               audit_store=audit, validate_fn=_ok_validator)
    assert out is not None and out["root_cause"] == "empty_buy_pool"
    rows = _recovery_rows(audit)
    assert len(rows) == 1 and rows[0]["detail"]["root_cause"] == "empty_buy_pool"
    notes = _notifications(store)
    assert len(notes) == 1
    blob = notes[0]["title"] + notes[0]["body"]
    assert "Geen koopmoment onder je max-prijs" in notes[0]["title"]
    assert "root_cause: empty_buy_pool" in notes[0]["body"]
    assert re.search(r"\d+\s*%", blob) is None


def test_fifty_cycles_one_audit_and_notification_per_day_per_cause(tmp_path):
    """#316 AC1: shifting deadlines, one cause → one row; a new cause or day is another."""
    store, audit, cache, notifier = _stores(tmp_path)
    status = CompletionStatus(status="missed", gap_pp=50.0, reason="missed")
    base_plan = _missed_plan()

    def catch(slots_used: int, target: float = 73.0) -> CatchUpResult:
        return CatchUpResult(
            plan=base_plan, feasible=False, target_soc=target, kwh_short=2.0,
            slots_used=slots_used, reason="catch-up",
            note=f"would have targeted {target:.0f}%",
        )

    def cycle(*, now, deadline, slots_used, soc_pct=30.0, cap_blocked=False,
              validate_fn=_ok_validator):
        plan = replace(base_plan, deadline=deadline)
        return _run(
            plan=plan, now=now, soc_pct=soc_pct, cache_store=cache, notifier=notifier,
            audit_store=audit, validate_fn=validate_fn, precomputed_catch=catch(slots_used),
            precomputed_status=status, cap_blocked=cap_blocked,
        )

    now = T0 + 4 * SLOT
    for i in range(50):
        cycle(now=now, deadline=DEADLINE + i * SLOT, slots_used=0)
    assert len(_recovery_rows(audit)) == 1
    assert len(_notifications(store)) == 1
    assert _recovery_rows(audit)[0]["detail"]["root_cause"] == "empty_buy_pool"
    empty_note = _notifications(store)[0]
    assert "Geen koopmoment onder je max-prijs" in empty_note["title"]
    assert re.search(r"\d+\s*%", empty_note["title"] + empty_note["body"]) is None

    # Same day, different cause (battery on the reserve floor) → a second row and push.
    cycle(now=now, deadline=DEADLINE + 60 * SLOT, slots_used=0, soc_pct=10.0)
    assert len(_recovery_rows(audit)) == 2
    assert len(_notifications(store)) == 2
    assert _recovery_rows(audit)[-1]["detail"]["root_cause"] == "floor"
    assert "Batterij op vloer" in _notifications(store)[-1]["title"]

    # Cap is its own cause, even when the pool is also empty.
    cycle(now=now, deadline=DEADLINE + 61 * SLOT, slots_used=0, cap_blocked=True)
    assert len(_recovery_rows(audit)) == 3
    assert _recovery_rows(audit)[-1]["detail"]["root_cause"] == "cap"
    assert "Dagelijkse schakellimiet" in _notifications(store)[-1]["body"]

    # Fail-safe is audited once and not pushed (a rejected catch-up stays quiet).
    for i in range(50):
        cycle(now=now, deadline=DEADLINE + (70 + i) * SLOT, slots_used=0,
              validate_fn=_unsafe_validator)
    assert len(_recovery_rows(audit)) == 4
    assert _recovery_rows(audit)[-1]["detail"]["root_cause"] == "fail_safe"
    assert "Fail-safe actief" not in "".join(n["title"] for n in _notifications(store))
    assert len(_notifications(store)) == 3

    # Next local day, same empty-pool cause → a new row and a new push.
    tomorrow = now + timedelta(days=1)
    for i in range(50):
        cycle(now=tomorrow, deadline=DEADLINE + timedelta(days=1, minutes=15 * i), slots_used=0)
    assert len(_recovery_rows(audit)) == 5
    assert len(_notifications(store)) == 4
    assert _recovery_rows(audit)[-1]["detail"]["root_cause"] == "empty_buy_pool"


def test_recovery_dedupe_follows_site_local_day_not_utc_date(tmp_path):
    """#316: dedupe day is `now.astimezone(site_tz).date()`, not the UTC calendar date."""
    store, audit, cache, notifier = _stores(tmp_path)
    status = CompletionStatus(status="missed", gap_pp=50.0, reason="missed")
    base_plan = _missed_plan()
    catch = CatchUpResult(
        plan=base_plan, feasible=False, target_soc=73.0, kwh_short=2.0,
        slots_used=0, reason="catch-up", note="no slots",
    )

    def cycle(now: datetime) -> None:
        asyncio.run(_run_recovery(
            base_plan, now, soc_pct=30.0, prices=_priced_out_prices(), enabled=True, tz=AMS,
            cache_store=cache, notifier=notifier, audit_store=audit, validate_fn=_ok_validator,
            precomputed_catch=catch, precomputed_status=status, **SIZING,
        ))

    # 22:30 UTC = 23:30 Europe/Amsterdam on 2026-01-15.
    cycle(datetime(2026, 1, 15, 22, 30, tzinfo=UTC))
    # 23:30 UTC = 00:30 on 2026-01-16 in Amsterdam — new local day, same root cause.
    cycle(datetime(2026, 1, 15, 23, 30, tzinfo=UTC))
    assert len(_recovery_rows(audit)) == 2
    assert len(_notifications(store)) == 2
    assert {r["detail"]["root_cause"] for r in _recovery_rows(audit)} == {"empty_buy_pool"}
