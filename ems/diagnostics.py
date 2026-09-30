"""System diagnostics (SPEC §13 setup/readiness): a flat list of named checks the UI renders so
an operator can see at a glance whether the EMS is wired up correctly. Pure — the API gathers the
facts (store reachability, probed battery, data quality) and passes them in, so this is unit-tested
with plain values and never touches I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

from ems.alerts import CRITICAL_SIGNALS  # single source of truth (no drift)

# Severity ordering for rolling up an overall status.
_RANK = {"ok": 0, "warn": 1, "fail": 2}
# Friendly per-signal labels for the live sensor checks.
_SIGNAL_LABEL = {
    "grid": "Sensor: grid (P1)",
    "solar": "Sensor: solar",
    "ev": "Sensor: EV",
    "battery": "Sensor: battery power",
    "soc": "Sensor: battery SoC",
    "prices": "Prices (Tibber)",
    "forecast": "Solar forecast",
}


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    status: str  # "ok" | "warn" | "fail"
    detail: str

    def __post_init__(self) -> None:
        # Fail loudly at construction on a typo'd status — otherwise overall_status would
        # silently rank an unknown status as 'ok' and show green over a real problem.
        if self.status not in _RANK:
            raise ValueError(f"invalid Check status: {self.status!r}")

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "status": self.status, "detail": self.detail}


def overall_status(checks: list[Check]) -> str:
    """Worst status across all checks (fail > warn > ok); 'ok' for an empty list. Statuses are
    validated in Check.__post_init__, so _RANK[s] cannot KeyError here."""
    return max((c.status for c in checks), key=lambda s: _RANK[s], default="ok")


def _battery_check(*, battery_ok: bool, p1_paired: bool, battery_present: bool) -> Check:
    """Battery-driver readiness: ok vs missing driver vs probe failure (do not conflate)."""
    if battery_ok:
        detail = f"probed; P1 {'paired' if p1_paired else 'not paired'}"
        status = "ok"
    elif battery_present:
        # Driver object exists but probe() raised — Indevolt IP/power/network, not "no driver".
        detail = "battery unreachable — probe failed (check Indevolt IP and power)"
        status = "warn"
    else:
        detail = "no battery driver — read-only"
        status = "warn"
    return Check("battery", "Battery driver", status, detail)


def build_diagnostics(
    *,
    dev_mode: str,
    dry_run: bool,
    data_quality: str,
    prices_ok: bool,
    forecast_ok: bool,
    battery_ok: bool,
    p1_paired: bool,
    plan_ok: bool,
    store_ok: bool,
    settings_store_ok: bool,
    auth_required: bool,
    identity_auth: bool = False,
    freshness: dict[str, str] | None = None,
    ev_guard_blind: bool = False,
    dry_run_block_reason: str | None = None,
    battery_present: bool | None = None,
) -> list[Check]:
    dq_status = {"complete": "ok", "degraded": "warn", "price_fallback": "warn"}.get(
        data_quality, "fail"
    )
    mode_detail = f"{dev_mode}, dry-run {'on' if dry_run else 'off'}"
    if dry_run_block_reason:
        mode_detail = f"{mode_detail} — {dry_run_block_reason}"
    # Config override of an ON operational toggle is warn so System shows why writes stay off.
    mode_status = "warn" if dry_run_block_reason else "ok"
    # Default: if battery_ok then a driver was present; if not, treat as missing unless told.
    # Callers that probe and catch failures must pass battery_present=True so we don't mislabel
    # "unreachable" as "no battery driver".
    present = battery_ok if battery_present is None else battery_present
    checks = [
        Check("mode", "Run mode", mode_status, mode_detail),
        Check("history_store", "History store", "ok" if store_ok else "fail",
              "reachable" if store_ok else "not reachable — history/UI degraded"),
        Check("settings_store", "Settings store", "ok" if settings_store_ok else "warn",
              "reachable" if settings_store_ok else "not reachable — settings won't persist"),
        Check("prices", "Electricity prices", "ok" if prices_ok else "warn",
              "price source configured" if prices_ok else "no price source — arbitrage disabled"),
        Check("forecast", "Solar forecast", "ok" if forecast_ok else "warn",
              "forecast source configured" if forecast_ok else "no forecast source"),
        _battery_check(
            battery_ok=battery_ok, p1_paired=p1_paired, battery_present=present,
        ),
        Check("data_quality", "Data quality", dq_status, data_quality),
        Check("planner", "Planner", "ok" if plan_ok else "warn",
              "producing a plan" if plan_ok else "no plan (missing prices?)"),
        # Identity auth (users/roles) supersedes the legacy shared-token gate: once the identity
        # store is wired (always, in production) EVERY request needs a signed-in user or an access
        # token, so the row reports that truthful state at ok. The legacy branch (auth_store is
        # None — old tests / shared-token-only deployments) keeps the pre-identity copy.
        Check("auth", "Write protection", "ok",
              "identity auth active — every request requires a signed-in user or access token"
              if identity_auth
              else "protected by a token" if auth_required
              else "open — set a Web access token in Settings to require one for writes"),
    ]
    # The car-charging guard (hold the battery to standby so it won't feed the car) can only fire if
    # it can SEE the car — i.e. the EV meter is configured. On + blind = a silent misconfiguration.
    if ev_guard_blind:
        checks.append(Check(
            "car_guard", "Car-charging guard", "warn",
            "on, but no EV meter is configured — it can't detect the car. Set the EV meter IP in "
            "Settings → Meters, or the battery may discharge into the car."))
    # Per-signal live sensor visibility: shows exactly which devices are reporting (the "senses").
    for sig, state in (freshness or {}).items():
        if state == "fresh":
            status = "ok"
        elif state == "missing":
            status = "fail" if sig in CRITICAL_SIGNALS else "warn"
        else:  # stale
            status = "fail" if sig in CRITICAL_SIGNALS else "warn"
        label = _SIGNAL_LABEL.get(sig, f"Sensor: {sig}")
        checks.append(Check(f"sensor.{sig}", label, status, state))
    return checks
