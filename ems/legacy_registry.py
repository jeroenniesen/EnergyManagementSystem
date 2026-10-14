"""Retirement register for retained legacy / compatibility paths (B-85 / #83).

Every retained entry must name an owner, why it still exists, how we know it is still used,
and the concrete trigger that lets us delete it. Entries with ``status="removed"`` document
already-deleted paths so they are not reintroduced without deliberate review.

Human-readable companion: ``docs/legacy-retirement.md``. Add a new entry here **before**
keeping a compatibility path; do not delete retained paths until their removal trigger fires.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Status = Literal["retained", "removed", "ignored"]


@dataclass(frozen=True)
class LegacyEntry:
    id: str
    """Stable short id (kebab-case)."""
    path: str
    """Repo-relative path, symbol, settings key, or glob describing the surface."""
    owner: str
    """Team/area that may change or retire this path."""
    reason: str
    """Why it still exists (or why it was removed)."""
    usage_signal: str
    """How we know whether anything still depends on it."""
    removal_trigger: str
    """Concrete condition that authorises deletion (or confirms stay-gone)."""
    status: Status = "retained"
    """retained = keep until trigger; removed = already gone; ignored = gitignored scratch."""


# Ordered for docs readability — group by theme, not alpha.
LEGACY_REGISTRY: tuple[LegacyEntry, ...] = (
    # --- Forecast snapshots (write retired; reads for historic installs) ---
    LegacyEntry(
        id="forecast-snapshots-table",
        path="ems/storage/history.py::forecast_snapshots",
        owner="storage / replay",
        reason=(
            "Write path retired for the prediction ledger (design §3.3); table kept so "
            "pre-migration DBs and retention purge still work, and migration v3 can backfill."
        ),
        usage_signal=(
            "CREATE TABLE IF NOT EXISTS + purge in HistoryStore; upsert_forecast_snapshot / "
            "forecasts_between only called from tests and migration helpers — not sense/recorder."
        ),
        removal_trigger=(
            "Drop table + helpers after one full retention window where every production DB "
            "has forecast_ledger solar rows covering the same horizon (no empty-ledger "
            "fallback hits in replay for ≥1 release)."
        ),
    ),
    LegacyEntry(
        id="forecast-snapshots-read-fallback",
        path="ems/replay.py::_day_ahead_forecast",
        owner="replay / insights",
        reason=(
            "Replay/what-if fall back to forecast_snapshots when the canonical ledger has no "
            "rows for the window (historic installs / incomplete migration)."
        ),
        usage_signal=(
            "replay._day_ahead_forecast SELECT; analysis._legacy_snapshot_row; "
            "test_replay / test_forecast_ledger / test_analysis."
        ),
        removal_trigger=(
            "Same as forecast-snapshots-table — delete fallback branch once ledger-only is "
            "proven for retained history."
        ),
    ),
    LegacyEntry(
        id="forecast-snapshots-upsert-helper",
        path="ems/storage/history.py::HistoryStore.upsert_forecast_snapshot",
        owner="storage",
        reason=(
            "Deprecated write helper retained so migration/tests can seed the legacy table; "
            "production recorder must not call it."
        ),
        usage_signal="Call sites only under ems/tests/ (grep upsert_forecast_snapshot).",
        removal_trigger="Remove with forecast-snapshots-table once tests seed ledger rows only.",
    ),
    # --- EV quick-advice (superseded by /api/car/plan) ---
    LegacyEntry(
        id="ev-quick-advice-endpoint",
        path="GET /api/advisor/ev-charge",
        owner="web / car",
        reason=(
            "Single-deadline quick advice superseded by schedule-aware GET /api/car/plan; "
            "kept for any external/script clients that still hit the old path."
        ),
        usage_signal=(
            "React SPA has zero references; still in EXEMPT/perf path lists, test_ev_advisor, "
            "tools/perf_check BATCH_PATHS."
        ),
        removal_trigger=(
            "Remove endpoint + advise_charge_window wiring after one release with a "
            "410/deprecation note (or confirm no callers via access logs / support). "
            "Keep ev.charge_kwh for #181."
        ),
    ),
    LegacyEntry(
        id="ev-departure-time-setting",
        path="ev.departure_time",
        owner="settings / car",
        reason=(
            "Single daily departure used only by deprecated /api/advisor/ev-charge; "
            "schedule.ready_by is the live source for /api/car/plan."
        ),
        usage_signal=(
            "Read only in api.advisor_ev_charge; still in SETTINGS_SCHEMA + e2e field lists."
        ),
        removal_trigger=(
            "Delete with ev-quick-advice-endpoint; migrate any stored value into "
            "schedule if needed."
        ),
    ),
    LegacyEntry(
        id="ev-advisor-module",
        path="ems/ev_advisor.py",
        owner="car",
        reason="Pure math behind the deprecated quick-advice endpoint.",
        usage_signal="Imported by api.advisor_ev_charge and test_ev_advisor only.",
        removal_trigger="Delete with ev-quick-advice-endpoint (car plan uses ems/ev_planner.py).",
    ),
    # --- Research / test-only simulation ---
    LegacyEntry(
        id="sim-backtest-harness",
        path="ems/sim.py",
        owner="planner research",
        reason=(
            "Offline charging-algorithm backtest harness (SPEC §8 / §14). Not on the control "
            "path; used by test_sim and docs/charging-algorithm-research.md evidence."
        ),
        usage_signal="Imported only by ems/tests/test_sim.py (and docs narrative).",
        removal_trigger=(
            "Keep while adaptive/summer regressions rely on nl_scenarios; retire only if "
            "replaced by a ledger-backed replay fixture suite that covers the same claims."
        ),
    ),
    LegacyEntry(
        id="planner-optimal-dp",
        path="ems/planner/optimal.py",
        owner="planner",
        reason=(
            "Research DP yardstick — tested but never dispatched. Removed in B-27 / #76; "
            "evidence kept in docs/charging-algorithm-research.md."
        ),
        usage_signal="Path must not exist; test_legacy_registry asserts absence.",
        removal_trigger=(
            "Stay gone — do not reintroduce without a live planner.mode that selects it."
        ),
        status="removed",
    ),
    # --- Compat settings / auth ---
    LegacyEntry(
        id="price-includes-all-fees-alias",
        path="grid_fees.tibber_total_includes_all",
        owner="prices / settings",
        reason=(
            "Legacy settings key renamed to grid_fees.price_includes_all_fees (#119); "
            "alias still accepted for one-release upgrades."
        ),
        usage_signal=(
            "LEGACY_PRICE_INCLUDES_ALL_FEES_KEY in ems/tariffs.py + settings validate/effective; "
            "test_price_includes_all_fees_migration."
        ),
        removal_trigger=(
            "Drop alias after ≥1 release where Settings UI only writes the canonical key and "
            "support confirms no store still has the legacy key alone."
        ),
    ),
    LegacyEntry(
        id="legacy-shared-token-tier-cap",
        path="ems/web/authz.py::_LEGACY_ACCESS_CAP",
        owner="auth",
        reason=(
            "Migrated/shared bearer tokens without an explicit tier still cap at operate "
            "(pre-identity-system behaviour)."
        ),
        usage_signal="authz.required_tier when token_tier is None; onboard migrate_token path.",
        removal_trigger=(
            "Remove when shared-token migration is mandatory and every token row has a tier "
            "(no None-tier access tokens in AuthStore)."
        ),
    ),
    # --- Forward-looking seams that look like dead code until M6 ---
    LegacyEntry(
        id="planner-mode-ml-advisory",
        path="ems/domain.py::PlannerMode + planner.mode ml|advisory",
        owner="planner / ml",
        reason=(
            "M6 seam: enum + settings options exist; adapters fall back to rule_based until "
            "ml/advisory ship. Not dead — deliberately disabled_options in Settings."
        ),
        usage_signal=(
            "planner/factory._UNAVAILABLE_MODES; settings disabled_options; "
            "test_planner_port / test_settings."
        ),
        removal_trigger=(
            "Do not delete the enum. Remove disabled_options when M6 adapters register; "
            "only delete ml/advisory tokens if the product drops those modes."
        ),
    ),
    # --- Temporary / scratch artifacts ---
    LegacyEntry(
        id="root-validation-screenshots",
        path="/ems-dashboard*-validation.png,/behavior-panels.png",
        owner="repo hygiene",
        reason="Ad-hoc browser/agent validation PNGs previously dropped at repo root.",
        usage_signal="Listed in .gitignore; must not appear in git ls-files.",
        removal_trigger="Already ignored — delete any local copies; no git tracking.",
        status="ignored",
    ),
    LegacyEntry(
        id="root-task-reports",
        path="/task-*-report.md",
        owner="repo hygiene",
        reason="Agent/SDD scratch reports sometimes written at repo root.",
        usage_signal=".gitignore /task-*-report.md (and /task-1-report.md); not tracked.",
        removal_trigger="Already ignored — keep pattern; never commit.",
        status="ignored",
    ),
)


def by_id() -> dict[str, LegacyEntry]:
    return {e.id: e for e in LEGACY_REGISTRY}


def retained() -> tuple[LegacyEntry, ...]:
    return tuple(e for e in LEGACY_REGISTRY if e.status == "retained")


def removed() -> tuple[LegacyEntry, ...]:
    return tuple(e for e in LEGACY_REGISTRY if e.status == "removed")
