"""Alerts + per-plan data-quality badge (SPEC §9.3, §8.11). Pure functions over the current
freshness snapshot + dry-run + the controller's decision outcome — easy to test, no I/O.

Every alert answers four questions (BACKLOG B-37 / B-09): what happened (`message`), is my
home/battery safe (`safe`), what EMS is doing (`ems_doing`), and what I can do (`action` —
"nothing needed, EMS handles this automatically" is a complete, honest answer, not a cop-out).
No alert may describe a condition without a next step.

Copy rules (B-09 / issue #73):
- Name the real device (P1 meter, Indevolt battery) — never "check in Home Assistant".
- Never promise more than failsafe guarantees: no "the battery is safe" / "nothing changes"
  without a confirmed AUTO. Stale/missing critical signals without confirmed AUTO must not
  say "safe mode".
- Critical-signal failsafe is ACTIVE AUTO (self-use), not "pause commands" (issue case c).
- Non-critical signals (prices / forecast / solar / …) must NOT claim a self-use fallback —
  control only fails safe on CRITICAL_SIGNALS (issue #79 review).

Live-prices gate copy (issue #126 / PR #148): operational without a live Tibber source stays
in watch mode; a Tibber outage while live forces self-use; dry-run outages never claim a
battery write. Device-health strip maps those alert keys onto the prices row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ems.sources.prices import MockPriceSource

# Signals whose staleness/absence makes control unsafe (can't reconstruct load / know SoC).
CRITICAL_SIGNALS = ("grid", "soc")

# Exact resident-facing copy from #126 (UX Designer + Safety & QA, 27-09-2026).
_MSG_NO_LIVE_PRICES = (
    "Kijkmodus: geen actuele prijzen van Tibber, EMS stuurt de batterij niet."
)
_MSG_TIBBER_DOWN_LIVE = (
    "Geen actuele prijzen van Tibber sinds {hhmm}. EMS heeft de batterij op eigen "
    "zelfverbruik gezet tot de prijzen terug zijn. Je hoeft niets te doen."
)
_MSG_TIBBER_DOWN_LIVE_UNCONFIRMED = (
    "Geen actuele prijzen van Tibber sinds {hhmm}. Laatst bekende stand: onbekend. "
    "Je hoeft niets te doen."
)
_MSG_TIBBER_DOWN_WATCH = (
    "Geen actuele prijzen van Tibber sinds {hhmm}. EMS kijkt alleen mee en verandert "
    "niets aan je batterij."
)


@dataclass(frozen=True)
class Alert:
    key: str
    severity: str  # info | warning | critical
    message: str
    safe: str  # is-my-home/battery-safe answer, plain language, no jargon, no blame
    action: str  # the one thing the user can do — an automatic-behaviour reassurance counts
    ems_doing: str  # what EMS is doing / will do in this situation (failsafe intent)


# Emotionally-complete signal messages (B-09). Critical signals (grid/soc): EMS actively commands
# AUTO / self-use when data is unsafe — distinguish confirmed vs not-yet-confirmed.
# {state} is filled with "unavailable" (missing) or "delayed" (stale).
_SIGNAL_INFO: dict[str, dict[str, str]] = {
    "grid": {
        "message": "P1 meter {state} — EMS can't see your grid usage, so it switches the "
                    "battery back to its own self-use (not yet confirmed).",
        "safe": "EMS is directing the battery to self-use; that return is not yet confirmed.",
        "ems_doing": "EMS actively commands the battery's own self-use and retries the P1 "
                     "meter automatically.",
        "action": "Nothing needed — EMS retries automatically. If this lasts past an hour, "
                   "check the HomeWizard P1 meter's power and network.",
        "message_confirmed_auto": "P1 meter {state} — EMS can't see your grid usage; the "
                                  "battery is confirmed in its own self-use until it returns.",
        "safe_confirmed_auto": "EMS has confirmed the battery is in its own self-use mode "
                               "while the P1 meter is {state}.",
        "ems_doing_confirmed_auto": "EMS is holding confirmed self-use and retries the P1 "
                                    "meter automatically.",
    },
    "soc": {
        "message": "Battery level {state} — EMS won't plan blind, so it switches the Indevolt "
                    "battery back to its own self-use (not yet confirmed).",
        "safe": "EMS is directing the battery to self-use; that return is not yet confirmed.",
        "ems_doing": "EMS actively commands the battery's own self-use and retries the Indevolt "
                     "reading automatically.",
        "action": "Nothing needed — EMS retries automatically. If this lasts past an hour, "
                   "check the Indevolt battery's power and network.",
        "message_confirmed_auto": "Battery level {state} — EMS won't plan blind; the battery "
                                  "is confirmed in its own self-use until the reading returns.",
        "safe_confirmed_auto": "EMS has confirmed the battery is in its own self-use mode "
                               "while the battery level is {state}.",
        "ems_doing_confirmed_auto": "EMS is holding confirmed self-use and retries the Indevolt "
                                    "reading automatically.",
    },
    "solar": {
        "message": "Solar meter {state} — solar accounting is less precise; battery control "
                    "continues.",
        "safe": "This only affects solar accounting, not whether EMS can command the battery.",
        "ems_doing": "EMS keeps controlling the battery; only solar totals may be less precise.",
        "action": "Nothing needed — EMS keeps controlling the battery normally. If it persists, "
                   "check the HomeWizard solar meter's power and network.",
    },
    "ev": {
        "message": "Car meter {state} — the car-protection guard is paused; battery control "
                    "continues.",
        "safe": "The battery itself is unaffected; only the car-charging guard is paused.",
        "ems_doing": "EMS keeps the normal battery plan; the car-charging guard resumes when "
                     "the meter reports again.",
        "action": "Nothing needed — the guard resumes once the car meter reports again. Check "
                   "the HomeWizard car meter's power and network if this continues for hours.",
    },
    "battery": {
        "message": "Battery power reading {state} — fine-grained SoC decisions may lag.",
        "safe": "EMS still has the last known mode; only fine-grained power-based decisions "
                "may lag briefly.",
        "ems_doing": "EMS keeps the current plan and catches up once Indevolt readings resume.",
        "action": "Nothing needed — EMS catches up automatically once readings resume. Check "
                   "the Indevolt battery's power and network if this persists.",
    },
    # Non-critical: stale prices/forecast do NOT trip failsafe — never claim self-use (#79).
    "prices": {
        "message": "Electricity prices {state} — EMS is working from an older price curve, so "
                    "price-based plans may be less precise.",
        "safe": "This does not by itself put the battery into self-use; only critical meter "
                "or SoC problems do that.",
        "ems_doing": "EMS keeps the current plan on the last known prices and refreshes Tibber "
                     "when it can.",
        "action": "Nothing needed — prices refresh automatically. If this lasts past an hour, "
                   "check your Tibber token and connection in Settings.",
    },
    "forecast": {
        "message": "Solar forecast {state} — today's solar outlook may be outdated.",
        "safe": "This does not by itself put the battery into self-use; battery control "
                "continues on the last forecast.",
        "ems_doing": "EMS keeps planning with the last forecast and will refresh Solcast / "
                     "Forecast.Solar on the next schedule window.",
        "action": "Nothing needed — the forecast refreshes on its daylight schedule. If this "
                   "lasts into the afternoon, check solar settings and Solcast credentials.",
    },
}
_STATE_WORD = {"missing": "unavailable", "stale": "delayed"}
# Fallback copy for any signal key not covered above, so a new signal never ships without an
# answer to "is my home safe" / "what is EMS doing" / "what can I do" (B-37/B-09).
# Deliberately does NOT claim self-use — only CRITICAL_SIGNALS do that.
_DEFAULT_SIGNAL_SAFE = (
    "EMS keeps control when it can; only a missing P1 meter or battery level forces self-use."
)
_DEFAULT_SIGNAL_EMS_DOING = (
    "EMS retries the missing signal automatically and keeps the current plan where it is safe."
)
_DEFAULT_SIGNAL_ACTION = (
    "Nothing needed — EMS retries automatically. Check the affected meter's or battery's "
    "power and network if this persists past an hour."
)


def prices_ok_for_quality(
    price_source: object | None, *, operational_requested: bool,
) -> bool:
    """Whether `_data_quality` may treat prices as OK toward `complete` (#126).

    A missing source is never OK. Mock/demo prices are OK only while operational control is
    *not* requested — operational + mock must not report `complete`. A live Tibber source that
    reports `unavailable_since()` is not OK either.
    """
    if price_source is None:
        return False
    if isinstance(price_source, MockPriceSource):
        return not operational_requested
    unavailable = getattr(price_source, "unavailable_since", None)
    if callable(unavailable) and unavailable() is not None:
        return False
    return True


def _hhmm(ts: datetime, site_tz: ZoneInfo | None) -> str:
    local = ts.astimezone(site_tz) if site_tz is not None else ts
    return local.strftime("%H:%M")


def derive_alerts(
    freshness: dict[str, str],
    *,
    dry_run: bool,
    decision_outcome: str | None,
    confirmed_auto: bool = False,
    last_command_unconfirmed: bool = False,
    price_horizon_ok: bool | None = None,
    control_overrun: bool = False,
    mock_prices_blocked_operational: bool = False,
    tibber_unavailable_since: datetime | None = None,
    site_tz: ZoneInfo | None = None,
) -> list[Alert]:
    """Build the calm alert list for the current snapshot.

    `confirmed_auto`: True only when last confirmed action AND observed mode are AUTO and the
    last command is not unconfirmed — gates wording that would otherwise over-claim failsafe.
    `last_command_unconfirmed`: sticky controller flag (production path for the unconfirmed
    alert — preview() never emits `unconfirmed`).
    `decision_outcome`: sticky write outcome from decide() (`failed_*` / `unconfirmed`); not
    from preview().
    `price_horizon_ok`: None = unknown/not yet checked (no alert); False = incomplete horizon.
    `control_overrun`: True while a recent control-cycle overrun is latched.
    `mock_prices_blocked_operational`: #126 case (a) — operational requested but no live
    Tibber source at startup, so EMS stays in watch mode.
    `tibber_unavailable_since`: #126 case (b)/(b-kijkmodus) — Tibber outage while a live
    price source is wired; wording depends on `dry_run` and `confirmed_auto`.
    """
    alerts: list[Alert] = []
    if mock_prices_blocked_operational:
        # #126 case (a): more specific than the generic dry_run info banner.
        alerts.append(Alert(
            "no_live_prices", "critical",
            _MSG_NO_LIVE_PRICES,
            safe="EMS stuurt de batterij niet zolang er geen actuele Tibber-prijzen zijn.",
            action="Zet live Tibber-prijzen aan in Instellingen (token) en herstart EMS.",
            ems_doing="EMS blijft in kijkmodus en schrijft niets naar de Indevolt-batterij.",
        ))
    elif dry_run:
        alerts.append(Alert(
            "dry_run_active", "info",
            "Watch-only mode — EMS observes and advises but won't change the battery.",
            safe="In this mode EMS only observes; it never writes to the battery.",
            action="Nothing needed — this is expected while you're evaluating EMS. Turn off "
                   "dry-run in Manage → Settings when you're ready for it to act.",
            ems_doing="EMS watches meters and the Indevolt battery but issues no mode changes.",
        ))
    if tibber_unavailable_since is not None:
        hhmm = _hhmm(tibber_unavailable_since, site_tz)
        if dry_run:
            # #126 case (b-kijkmodus): never claim a battery write / self-use command.
            # warning (not critical): EMS changes nothing in watch mode (#148 review F7).
            alerts.append(Alert(
                "tibber_prices_unavailable", "warning",
                _MSG_TIBBER_DOWN_WATCH.format(hhmm=hhmm),
                safe="In kijkmodus verandert EMS niets aan je batterij.",
                action="Niets nodig — EMS probeert Tibber automatisch opnieuw. Check je Tibber-"
                       "verbinding in Instellingen als dit aanhoudt.",
                ems_doing="EMS kijkt alleen mee tot actuele Tibber-prijzen terug zijn.",
            ))
        elif confirmed_auto:
            alerts.append(Alert(
                "tibber_prices_unavailable", "critical",
                _MSG_TIBBER_DOWN_LIVE.format(hhmm=hhmm),
                safe="De batterij draait op eigen zelfverbruik tot Tibber-prijzen terug zijn.",
                action="Niets nodig — EMS probeert Tibber automatisch opnieuw.",
                ems_doing="EMS houdt eigen zelfverbruik aan tot actuele prijzen terug zijn.",
            ))
        else:
            # AUTO not confirmed: no mode name anywhere in the resident-facing lines (#126).
            alerts.append(Alert(
                "tibber_prices_unavailable", "critical",
                _MSG_TIBBER_DOWN_LIVE_UNCONFIRMED.format(hhmm=hhmm),
                safe="Laatst bekende stand: onbekend — EMS claimt geen batterijstand.",
                action="Niets nodig — EMS probeert Tibber opnieuw en bevestigt de Indevolt-"
                       "batterij zodra dat kan.",
                ems_doing="EMS wacht op bevestiging van de Indevolt-batterij; de stand is nog "
                          "onbekend.",
            ))
    for sig, state in sorted(freshness.items()):
        if state in ("missing", "stale"):
            sev = "critical" if sig in CRITICAL_SIGNALS else "warning"
            info = _SIGNAL_INFO.get(sig, {})
            state_word = _STATE_WORD.get(state, state)
            if confirmed_auto and "message_confirmed_auto" in info:
                msg = info["message_confirmed_auto"].format(state=state_word)
                safe = info["safe_confirmed_auto"].format(state=state_word)
                ems_doing = info["ems_doing_confirmed_auto"]
            else:
                msg = info.get("message", f"{sig} signal {{state}}").format(state=state_word)
                safe = info.get("safe", _DEFAULT_SIGNAL_SAFE)
                ems_doing = info.get("ems_doing", _DEFAULT_SIGNAL_EMS_DOING)
            alerts.append(Alert(
                f"{sig}_{state}", sev, msg,
                safe=safe,
                action=info.get("action", _DEFAULT_SIGNAL_ACTION),
                ems_doing=ems_doing,
            ))
    # Write-failure alerts: prefer sticky decide() outcome; fall back to last_command_unconfirmed
    # so production /api/alerts can surface unconfirmed without relying on preview().
    if decision_outcome == "failed_unrecovered":
        alerts.append(Alert(
            "battery_write_failed_unrecovered", "critical",
            "A battery command AND the self-use recovery were both unconfirmed — this needs "
            "attention; check the Indevolt battery connection.",
            safe="The battery keeps its last confirmed mode — it isn't stuck mid-command, but "
                 "EMS also couldn't confirm the self-use fallback.",
            action="Check the Indevolt battery's power and network now; if it doesn't recover, "
                   "restart the Indevolt gateway or check the Indevolt app.",
            ems_doing="EMS stopped issuing new commands until it can confirm the battery again.",
        ))
    elif decision_outcome == "failed_recovered":
        alerts.append(Alert(
            "battery_write_failed_recovered", "warning",
            "A battery command didn't confirm, so EMS reverted to the battery's own self-use. "
            "No action needed.",
            safe="EMS confirmed the battery returned to its own self-use mode after the failed "
                 "command.",
            action="Nothing needed — EMS will retry the change on its own. Only check the "
                   "Indevolt battery's connection if this keeps happening.",
            ems_doing="EMS commanded self-use after the failed write and will retry later.",
        ))
    elif decision_outcome == "unconfirmed" or last_command_unconfirmed:
        alerts.append(Alert(
            "battery_command_unconfirmed", "warning",
            "A battery command was not confirmed — the Indevolt battery may still be catching "
            "up.",
            safe="EMS is not assuming a new mode until the battery confirms; the last "
                 "confirmed mode still applies.",
            action="Nothing needed — EMS retries automatically. If this keeps happening, check "
                   "the Indevolt battery's power and network.",
            ems_doing="EMS holds and will retry; it does not pile on more commands while this "
                      "is open.",
        ))
    if price_horizon_ok is False:
        alerts.append(Alert(
            "price_horizon_incomplete", "warning",
            "Price horizon incomplete — EMS can't plan the full day on Tibber prices yet.",
            safe="EMS will not run price-based battery moves until a complete price day is "
                 "available.",
            action="Nothing needed — prices refresh automatically. If this lasts into the "
                   "evening, check your Tibber connection in Settings.",
            ems_doing="EMS keeps self-use planning and waits for a full Tibber price horizon.",
        ))
    if control_overrun:
        # Hedge: the overrun latch is set even when dry-run / lifecycle / drain skip the AUTO
        # write, and the timed-out tick worker is shielded (not stopped).
        alerts.append(Alert(
            "control_overrun", "warning",
            "A control cycle ran too long — EMS is recovering the plan.",
            safe="EMS may command the battery back to its own self-use after an overrun; that "
                 "write is skipped in watch-only mode or before control is ready.",
            action="Nothing needed — EMS recovers automatically. If this keeps recurring, "
                   "check the Indevolt battery's network latency.",
            ems_doing="EMS flagged the over-budget cycle and will try self-use recovery when "
                      "it is allowed to command.",
        ))
    return alerts


def data_quality(
    freshness: dict[str, str],
    *,
    prices_ok: bool,
    forecast_ok: bool,
    prices_live: bool = True,
    operational: bool = False,
) -> str:
    """complete | degraded | price_fallback | unsafe (SPEC §8.11).

    Precedence (most severe first): unsafe > price_fallback > degraded > complete. So a missing
    price with a simultaneously-stale non-critical signal reports price_fallback (the per-signal
    staleness still surfaces separately as an alert).

    `prices_live` / `operational` (issue #79 Klaar-als #4): armed control on mock prices must
    never report `complete` — `price_fallback` instead. This is a badge/quality signal only;
    forcing dry-run / self-use when prices aren't live is owned by #126 / PR #148.
    """
    for sig in CRITICAL_SIGNALS:
        if freshness.get(sig, "missing") != "fresh":
            return "unsafe"  # can't safely reconstruct/plan
    if not prices_ok or (operational and not prices_live):
        return "price_fallback"
    if not forecast_ok or any(state != "fresh" for state in freshness.values()):
        return "degraded"
    return "complete"
