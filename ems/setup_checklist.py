"""Read-only setup checklist for onboarding (#290): plain Dutch items + layered readiness.

Pure — callers gather facts (freshness, probes, price source kind) and pass them in. Uses
``ems.readiness.compute_readiness`` for watch vs control layers; does not touch settings or control.
"""
from __future__ import annotations

from dataclasses import dataclass

from ems.readiness import Readiness, compute_readiness

# Internal status keys → exact homeowner-facing words (issue #290).
_STATUS_LABEL = {
    "werkt": "Werkt",
    "ontbreekt": "Ontbreekt",
    "optioneel": "Optioneel",
    "nodig_om_te_sturen": "Nodig om te sturen",
}


@dataclass(frozen=True)
class SetupChecklistItem:
    key: str
    sentence: str
    status: str  # werkt | ontbreekt | optioneel | nodig_om_te_sturen
    button_label: str
    settings_section: str
    blocks_watch: bool

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "sentence": self.sentence,
            "status": _STATUS_LABEL[self.status],
            "button_label": self.button_label,
            "settings_section": self.settings_section,
        }


@dataclass(frozen=True)
class SetupChecklist:
    steps_remaining: int
    watch_ready: bool
    headline: str
    readiness: Readiness
    items: tuple[SetupChecklistItem, ...]

    def to_dict(self) -> dict:
        return {
            "steps_remaining": self.steps_remaining,
            "watch_ready": self.watch_ready,
            "headline": self.headline,
            "readiness": self.readiness.to_dict(),
            "items": [i.to_dict() for i in self.items],
        }


def build_setup_checklist(
    *,
    store_ok: bool,
    sensing_ok: bool,
    plan_ok: bool,
    data_quality: str,
    plan_valid: bool,
    operational: bool,
    capability_ok: bool,
    freshness: dict[str, str] | None,
    battery_ok: bool,
    battery_present: bool,
    p1_paired: bool,
    soc_ready: bool,
    prices_live: bool,
    forecast_ok: bool,
    grid_fuse_unset: bool,
) -> SetupChecklist:
    """Assemble homeowner checklist items and derive steps until EMS can watch (sensing_ready)."""
    readiness = compute_readiness(
        store_ok=store_ok,
        sensing_ok=sensing_ok,
        plan_ok=plan_ok,
        data_quality=data_quality,
        plan_valid=plan_valid,
        operational=operational,
        capability_ok=capability_ok,
    )
    fresh = freshness or {}
    grid_fresh = fresh.get("grid") == "fresh"

    if battery_ok and soc_ready:
        battery_status = "werkt"
    else:
        battery_status = "ontbreekt"

    if grid_fresh and p1_paired:
        p1_status = "werkt"
    else:
        p1_status = "ontbreekt"

    prices_status = "werkt" if prices_live else "ontbreekt"
    forecast_status = "werkt" if forecast_ok else "optioneel"
    capability_status = "werkt" if capability_ok else "nodig_om_te_sturen"
    fuse_status = "nodig_om_te_sturen" if grid_fuse_unset else "werkt"

    items = (
        SetupChecklistItem(
            key="battery",
            sentence="EMS ziet je batterij en weet hoe vol ze is.",
            status=battery_status,
            button_label="Batterij instellen",
            settings_section="battery",
            blocks_watch=True,
        ),
        SetupChecklistItem(
            key="p1",
            sentence="De stroommeter op je meterkast stuurt actuele metingen door.",
            status=p1_status,
            button_label="Meter instellen",
            settings_section="meters",
            blocks_watch=True,
        ),
        SetupChecklistItem(
            key="prices",
            sentence="Je dynamische stroomprijzen komen binnen van Tibber.",
            status=prices_status,
            button_label="Prijzen instellen",
            settings_section="prices",
            blocks_watch=False,
        ),
        SetupChecklistItem(
            key="forecast",
            sentence="Er is een zonvoorspelling voor de komende dagen.",
            status=forecast_status,
            button_label="Zon instellen",
            settings_section="site",
            blocks_watch=False,
        ),
        SetupChecklistItem(
            key="capability",
            sentence="EMS heeft de grenzen van je batterij bevestigd.",
            status=capability_status,
            button_label="Batterij controleren",
            settings_section="battery",
            blocks_watch=False,
        ),
        SetupChecklistItem(
            key="grid_fuse",
            sentence="Je hoofdzekering staat in EMS, zodat laden veilig blijft.",
            status=fuse_status,
            button_label="Zekering bevestigen",
            settings_section="control",
            blocks_watch=False,
        ),
    )

    steps_remaining = sum(
        1 for i in items if i.status == "ontbreekt" and i.blocks_watch
    )
    watch_ready = readiness.sensing_ready
    if watch_ready:
        headline = "EMS kan meekijken"
    else:
        headline = f"Nog {steps_remaining} stappen tot EMS kan meekijken"

    return SetupChecklist(
        steps_remaining=steps_remaining,
        watch_ready=watch_ready,
        headline=headline,
        readiness=readiness,
        items=items,
    )
