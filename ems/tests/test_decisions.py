"""Pure mapper from raw audit rows → homeowner-facing decision-timeline events (2026-07-15 plan).
No I/O; the endpoint just feeds it audit_store rows."""
from ems.decisions import decision_events


def _row(id_, category, detail, summary="", ts="2026-07-15T10:00:00+00:00"):
    return {"id": id_, "ts": ts, "category": category, "summary": summary, "detail": detail}


def test_executed_plan_event():
    ev = decision_events([_row(1, "battery_decision",
                               {"outcome": "applied", "desired_mode": "charge",
                                "intent": "grid_charge_to_target",
                                "reason": "cheap window — charging"})])
    assert len(ev) == 1
    e = ev[0]
    assert e["id"] == "1" and e["time"] == "2026-07-15T10:00:00+00:00"
    assert "charg" in e["title"].lower()
    assert e["reason"] == "cheap window — charging"
    assert e["consequence"] and e["action"].lower().startswith("no action")
    assert e["severity"] == "info"


def test_economic_skip_event():
    ev = decision_events([_row(2, "battery_decision",
                               {"outcome": "economic_skip", "intent": "allow_self_consumption",
                                "desired_mode": "auto",
                                "reason": "no-trade: spread below break-even"})])
    e = ev[0]
    assert "skip" in e["title"].lower()
    assert "break-even" in e["reason"]
    assert "safe baseline" in e["consequence"].lower()
    assert e["severity"] == "info"
    assert e["action"].lower().startswith("no action")


def test_safety_fallback_event_is_a_warning():
    ev = decision_events([_row(3, "battery_decision",
                               {"outcome": "failed_recovered", "desired_mode": "charge",
                                "reason": "charge unconfirmed -> reverted to AUTO"})])
    e = ev[0]
    assert e["severity"] == "warning"
    assert "safe baseline" in e["consequence"].lower()
    assert "check" in e["action"].lower()


def test_no_action_held_event():
    ev = decision_events([_row(4, "battery_decision",
                               {"outcome": "cap_reached", "desired_mode": "charge",
                                "reason": "daily switch cap reached; holding"})])
    e = ev[0]
    assert "held" in e["title"].lower()
    assert e["severity"] == "info"
    assert e["action"].lower().startswith("no action")


def test_shutdown_restore_event():
    ev = decision_events([_row(5, "shutdown_restore",
                               {"target": "auto", "confirmed": True},
                               summary="Graceful shutdown — restored battery to auto")])
    e = ev[0]
    assert e["severity"] == "info"
    assert e["action"].lower().startswith("no action")


def test_unrelated_categories_are_ignored():
    ev = decision_events([
        _row(6, "settings_change", {"keys": ["strategy.mode"]}),
        _row(7, "battery_decision", {"outcome": "applied", "desired_mode": "auto", "reason": "x"}),
    ])
    assert len(ev) == 1 and ev[0]["id"] == "7"


def test_missing_detail_never_raises():
    ev = decision_events([_row(8, "battery_decision", {})])
    assert len(ev) == 1
    assert ev[0]["action"].lower().startswith("no action")
