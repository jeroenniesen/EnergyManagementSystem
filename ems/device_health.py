"""Consumer-facing device / data-health summary (BACKLOG B-38 / issue #79).

Builds a compact per-source view (battery, P1, prices, solar forecast) with state, last-update
time, and a plain-language overall badge — separate from the §8.11 data-quality enum so the
dashboard can say "Deels verouderd" + which source without leaking tokens like "degraded".

Pure helpers over a freshness detail snapshot + a few flags; unit-tested with no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Dashboard consumer sources (issue #79 Klaar-als #1) — details for other sensors stay on System.
CONSUMER_SOURCES: tuple[str, ...] = ("battery", "grid", "prices", "forecast")

_SOURCE_LABEL = {
    "battery": "Batterij",
    "grid": "P1-meter",
    "prices": "Prijzen",
    "forecast": "Zonvoorspelling",
}


@dataclass(frozen=True)
class SourceHealth:
    key: str
    label: str
    state: str  # fresh | stale | missing
    updated_at: str | None  # ISO
    updated_hhmm: str | None  # local hh:mm for "bijgewerkt om …"
    age_seconds: float | None
    note: str | None = None  # plain-language qualifier (cached / mock / unreachable)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state,
            "updated_at": self.updated_at,
            "updated_hhmm": self.updated_hhmm,
            "age_seconds": self.age_seconds,
            "note": self.note,
        }


def _hhmm(ts: datetime | None, tz) -> str | None:
    if ts is None:
        return None
    local = ts.astimezone(tz) if tz is not None else ts
    return f"{local.hour:02d}:{local.minute:02d}"


def format_source_detail(source_key: str, hhmm: str | None) -> str:
    """e.g. 'prijzen van 14:00' — lowercase source name for the summary suffix."""
    name = {
        "battery": "batterij",
        "grid": "P1-meter",
        "prices": "prijzen",
        "forecast": "zonvoorspelling",
    }.get(source_key, source_key)
    if hhmm:
        return f"{name} van {hhmm}"
    return name


def build_device_health(
    detail: dict[str, dict],
    *,
    now: datetime,
    tz=None,
    battery_reachable: bool | None = None,
    prices_kind: str = "unknown",  # live | mock | none
    forecast_label: str | None = None,
    dry_run: bool = True,
    operational_mock_prices: bool = False,
    demo: bool = False,
) -> dict:
    """Return the device-health payload for /api/device-health and the dashboard snapshot.

    `detail` maps signal -> {state, updated_at (datetime|None), age_seconds (float|None)}.
    """
    sources: list[SourceHealth] = []
    for key in CONSUMER_SOURCES:
        info = detail.get(key) or {}
        state = str(info.get("state") or "missing")
        updated = info.get("updated_at")
        age = info.get("age_seconds")
        note: str | None = None
        if key == "battery" and battery_reachable is False:
            state = "missing" if state == "fresh" else state
            note = "niet bereikbaar"
        if key == "prices":
            if prices_kind == "mock":
                note = "demoprijzen" if demo or dry_run else "geen live Tibber-prijzen"
            elif prices_kind == "none":
                note = "geen prijsbron"
            elif state == "stale":
                note = "reserveprijzen / laatst bekende curve"
        if key == "forecast":
            if forecast_label and ("cached" in forecast_label or "fallback" in forecast_label
                                   or "budget held" in forecast_label):
                note = "cached / reservevoorspelling"
            elif state == "stale":
                note = "verouderde voorspelling"
        sources.append(SourceHealth(
            key=key,
            label=_SOURCE_LABEL.get(key, key),
            state=state,
            updated_at=updated.isoformat() if isinstance(updated, datetime) else (
                updated if isinstance(updated, str) else None
            ),
            updated_hhmm=_hhmm(updated if isinstance(updated, datetime) else None, tz),
            age_seconds=float(age) if age is not None else None,
            note=note,
        ))

    # Overall badge (consumer language). Precedence: demo → mock-prices critical → partial stale
    # → all current. "Deels verouderd" (never "Gedegradeerd") when any consumer source is off.
    stale_sources = [s for s in sources if s.state in ("stale", "missing")]
    if demo:
        badge = "demo"
        label = "Demo"
        detail_txt = "Cijfers komen niet van jouw huis — dit is demodata."
        severity = "warning"
    elif operational_mock_prices:
        # Armed on demoprijzen (#79 #4 / #126 case b) — critical, never "complete".
        badge = "mock_prices"
        label = "Geen actuele prijzen"
        detail_txt = (
            "Geen actuele prijzen van Tibber. EMS heeft de batterij op eigen zelfverbruik "
            "gezet tot de prijzen terug zijn. Je hoeft niets te doen."
        )
        severity = "critical"
    elif dry_run and prices_kind == "mock":
        # Watch-only without a live Tibber source (#126 case a) — kritiek kijkmodus-label.
        badge = "mock_prices"
        label = "Geen actuele prijzen"
        detail_txt = (
            "Kijkmodus: geen actuele prijzen van Tibber, EMS stuurt de batterij niet."
        )
        severity = "critical"
    elif stale_sources:
        badge = "partially_stale"
        label = "Deels verouderd"
        first = stale_sources[0]
        detail_txt = format_source_detail(first.key, first.updated_hhmm)
        severity = "critical" if first.key in ("grid", "battery") and first.state == "missing" \
            else "warning"
    else:
        badge = "current"
        label = "Alles actueel"
        detail_txt = "Batterij, P1-meter, prijzen en zonvoorspelling zijn bijgewerkt."
        severity = "ok"

    forecast = next((s for s in sources if s.key == "forecast"), None)
    return {
        "sources": [s.to_dict() for s in sources],
        "battery_reachable": battery_reachable,
        "forecast_age_seconds": forecast.age_seconds if forecast else None,
        "prices_kind": prices_kind,
        "summary": {
            "badge": badge,
            "label": label,
            "detail": detail_txt,
            "severity": severity,
        },
        "as_of": now.isoformat(),
    }
