"""B-85 / #83: retirement register is complete, retained paths exist, removed paths stay gone."""
from __future__ import annotations

from pathlib import Path

from ems.legacy_registry import LEGACY_REGISTRY, LegacyEntry, by_id, removed, retained

_ROOT = Path(__file__).resolve().parents[2]


def test_registry_ids_unique_and_complete():
    ids = [e.id for e in LEGACY_REGISTRY]
    assert len(ids) == len(set(ids))
    assert LEGACY_REGISTRY, "registry must not be empty"
    for e in LEGACY_REGISTRY:
        assert e.id and e.path and e.owner and e.reason
        assert e.usage_signal and e.removal_trigger
        assert e.status in ("retained", "removed", "ignored")
        # Done-when: every retained entry is actionable, not a wish.
        if e.status == "retained":
            assert len(e.removal_trigger) >= 20


def test_required_themes_are_registered():
    """Issue #83 / BACKLOG B-85 call out these surfaces explicitly."""
    reg = by_id()
    for required in (
        "forecast-snapshots-table",
        "forecast-snapshots-read-fallback",
        "ev-quick-advice-endpoint",
        "ev-departure-time-setting",
        "sim-backtest-harness",
        "planner-optimal-dp",
        "root-validation-screenshots",
        "root-task-reports",
    ):
        assert required in reg, required


def test_retained_source_files_exist():
    must_exist = {
        "sim-backtest-harness": "ems/sim.py",
        "ev-advisor-module": "ems/ev_advisor.py",
        "forecast-snapshots-read-fallback": "ems/replay.py",
        "forecast-snapshots-table": "ems/storage/history.py",
        "legacy-shared-token-tier-cap": "ems/web/authz.py",
        "price-includes-all-fees-alias": "ems/tariffs.py",
    }
    for entry_id, rel in must_exist.items():
        assert by_id()[entry_id].status == "retained"
        assert (_ROOT / rel).is_file(), rel


def test_removed_optimal_planner_stays_gone():
    entry = by_id()["planner-optimal-dp"]
    assert entry.status == "removed"
    assert not (_ROOT / "ems/planner/optimal.py").exists()
    assert not (_ROOT / "ems/tests/test_optimal.py").exists()


def test_docs_companion_and_gitignore_cover_scratch():
    assert (_ROOT / "docs/legacy-retirement.md").is_file()
    gitignore = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "ems-dashboard-validation.png" in gitignore
    assert "task-1-report.md" in gitignore or "task-*-report.md" in gitignore
    # Broadened pattern from #83 polish — any root task-N-report.md.
    assert "task-*-report.md" in gitignore


def test_helpers_partition_by_status():
    assert all(e.status == "retained" for e in retained())
    assert all(e.status == "removed" for e in removed())
    assert {e.id for e in retained()} | {e.id for e in removed()} | {
        e.id for e in LEGACY_REGISTRY if e.status == "ignored"
    } == {e.id for e in LEGACY_REGISTRY}


def test_ev_quick_advice_still_wired_until_trigger():
    """Retained: endpoint + setting must still be present (removal is a later PR)."""
    api = (_ROOT / "ems/web/api.py").read_text(encoding="utf-8")
    assert '/api/advisor/ev-charge' in api
    settings = (_ROOT / "ems/settings.py").read_text(encoding="utf-8")
    assert '"ev.departure_time"' in settings


def test_legacy_entry_frozen():
    e = LEGACY_REGISTRY[0]
    assert isinstance(e, LegacyEntry)
    try:
        e.id = "nope"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("LegacyEntry must be frozen")
