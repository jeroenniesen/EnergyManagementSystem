"""Resident must-charge override (#322 / audit F-41).

A temporary record ``{target_soc, deadline, created, expires}`` in the existing override KV.
The planner turns it into GRID_CHARGE slots through ``ems.planner.must_charge``; this module
only parses, stores and expires that record. It never writes the battery.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.planner.must_charge import GEEN_STURING

# Accepted deadline text, documented on GET /api/override/must-charge and in docs/api-reference.md:
#   * ISO-8601 with a timezone offset (``2026-01-16T07:00:00+01:00`` or ``Z``)
#   * ``HH:MM`` — the next occurrence strictly after now, in the site timezone
DEADLINE_FORMAT = (
    "ISO-8601 with a timezone offset, or HH:MM (next occurrence in the site timezone)"
)
MAX_AHEAD = timedelta(hours=24)
WATCH_ONLY_TEXT = "alleen kijken — niet uitgevoerd"

KEY_TARGET = "override.must_charge.target_soc"
KEY_DEADLINE = "override.must_charge.deadline"
KEY_CREATED = "override.must_charge.created"
KEY_EXPIRES = "override.must_charge.expires"
MUST_CHARGE_KEYS = (KEY_TARGET, KEY_DEADLINE, KEY_CREATED, KEY_EXPIRES)

AUDIT_START = "must_charge_override_start"
AUDIT_END = "must_charge_override_end"
AUDIT_CANCEL = "must_charge_override_cancel"

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class MustChargeOverride:
    target_soc: float
    deadline: datetime
    created: datetime
    expires: datetime

    def engages(self, now: datetime, soc_pct: float | None) -> bool:
        """True while the planner should still add grid-charge slots."""
        if now >= self.deadline or now >= self.expires:
            return False
        if soc_pct is None or soc_pct < 0 or soc_pct > 100:
            return False
        return soc_pct + 1e-9 < self.target_soc

    def end_cause(self, now: datetime, soc_pct: float | None) -> str | None:
        """``target`` or ``deadline`` once the override must end; None while it still applies.

        Unknown SoC does not end the record — the planner simply does not charge until SoC
        is known again.
        """
        if soc_pct is not None and soc_pct + 1e-9 >= self.target_soc:
            return "target"
        if now >= self.deadline or now >= self.expires:
            return "deadline"
        return None

    def seconds_remaining(self, now: datetime) -> int:
        if now >= self.deadline:
            return 0
        return max(0, int((self.deadline - now).total_seconds()))


def from_stored(raw: dict | None) -> MustChargeOverride | None:
    """Rebuild a record from the override KV. A bad row degrades to no override."""
    if not raw:
        return None
    try:
        target = float(raw.get(KEY_TARGET))
        deadline = datetime.fromisoformat(str(raw.get(KEY_DEADLINE)))
        created = datetime.fromisoformat(str(raw.get(KEY_CREATED)))
        expires = datetime.fromisoformat(str(raw.get(KEY_EXPIRES)))
    except (TypeError, ValueError):
        return None
    if deadline.tzinfo is None or created.tzinfo is None or expires.tzinfo is None:
        return None
    if not (0.0 <= target <= 100.0):
        return None
    return MustChargeOverride(
        target_soc=target, deadline=deadline, created=created, expires=expires,
    )


def validate_target_soc(value: object, floor_soc: float) -> tuple[float | None, str | None]:
    """Target must sit between the effective floor + 5 percentage points and 100."""
    low = float(floor_soc) + 5.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "must be a number"
    target = float(value)
    if not (low - 1e-9 <= target <= 100.0):
        return None, f"must be between {low:g} and 100"
    return target, None


def parse_deadline(
    raw: object, now: datetime, site_tz: ZoneInfo,
) -> tuple[datetime | None, str | None]:
    """Parse a deadline. Returns ``(deadline, None)`` or ``(None, error)``."""
    if not isinstance(raw, str) or not raw.strip():
        return None, f"must be {DEADLINE_FORMAT}"
    text = raw.strip()
    match = _HHMM.match(text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        local = now.astimezone(site_tz)
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate = candidate + timedelta(days=1)
        deadline = candidate.astimezone(now.tzinfo)
    else:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None, f"must be {DEADLINE_FORMAT}"
        if parsed.tzinfo is None:
            return None, f"must be {DEADLINE_FORMAT}"
        deadline = parsed.astimezone(now.tzinfo)
    if deadline <= now:
        return None, "must be in the future and at most 24 hours ahead"
    if deadline - now > MAX_AHEAD:
        return None, "must be in the future and at most 24 hours ahead"
    return deadline, None


def watch_only_execution(watch_only: bool) -> str | None:
    return WATCH_ONLY_TEXT if watch_only else None


def public_view(
    rec: MustChargeOverride | None,
    now: datetime,
    site_tz: ZoneInfo,
    *,
    watch_only: bool,
    unreachable_reason: str | None,
    active: bool,
) -> dict:
    execution = watch_only_execution(watch_only)
    base = {
        "active": active,
        "target_soc": None,
        "deadline": None,
        "created": None,
        "expires": None,
        "deadline_hhmm": None,
        "seconds_remaining": 0,
        "warning": None,
        "unreachable_reason": None,
        "watch_only": watch_only,
        "execution": execution,
        "deadline_format": DEADLINE_FORMAT,
    }
    if rec is None or not active:
        return base
    return {
        **base,
        "target_soc": rec.target_soc,
        "deadline": rec.deadline.isoformat(),
        "created": rec.created.isoformat(),
        "expires": rec.expires.isoformat(),
        "deadline_hhmm": rec.deadline.astimezone(site_tz).strftime("%H:%M"),
        "seconds_remaining": rec.seconds_remaining(now),
        "warning": unreachable_reason,
        "unreachable_reason": unreachable_reason,
    }


__all__ = [
    "AUDIT_CANCEL",
    "AUDIT_END",
    "AUDIT_START",
    "DEADLINE_FORMAT",
    "GEEN_STURING",
    "KEY_CREATED",
    "KEY_DEADLINE",
    "KEY_EXPIRES",
    "KEY_TARGET",
    "MUST_CHARGE_KEYS",
    "MustChargeOverride",
    "WATCH_ONLY_TEXT",
    "from_stored",
    "parse_deadline",
    "public_view",
    "validate_target_soc",
    "watch_only_execution",
]
