"""Shared pytest helpers for API app construction."""

from __future__ import annotations

from datetime import UTC, datetime

# Boot-time maintenance purges samples/notifications older than `history_retention_days`
# (default 90). Tests that seed fixed past dates must disable that purge so TestClient
# lifespan does not erase the fixture data when the wall clock moves forward — while
# keeping the default retention value so year-view labeling ("last 90 days") stays true.
NO_HISTORY_PURGE = {"history_purge_enabled": False, "history_backup_keep": 0}

# `_seed()` in test_export_package uses 2026-06-28; the package endpoint's default `days=90`
# window drifts with the wall clock — request a wide window in those tests.
EXPORT_PACKAGE_WIDE_DAYS = 400


def freeze_wall_clock_utc(monkeypatch, frozen_utc: datetime) -> None:
    """Pin EMS clock seams to a fixed UTC instant (prices/forecast mocks + API ``now()``)."""
    assert frozen_utc.tzinfo is not None
    frozen_utc = frozen_utc.astimezone(UTC)

    import ems.web.api as api_mod
    from ems.sources import forecast as forecast_mod
    from ems.sources import prices as prices_mod

    monkeypatch.setattr(prices_mod, "_utcnow", lambda: frozen_utc)
    monkeypatch.setattr(forecast_mod, "_utcnow", lambda: frozen_utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is UTC:
                return frozen_utc
            if tz is None:
                return frozen_utc.replace(tzinfo=None)
            return frozen_utc.astimezone(tz)

    monkeypatch.setattr(api_mod, "datetime", _FrozenDatetime)


def amsterdam_instant(
    year: int, month: int, day: int, hour: int, minute: int = 0
) -> datetime:
    """Naive wall-time in Europe/Amsterdam converted to a tz-aware instant."""
    from zoneinfo import ZoneInfo

    local = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Europe/Amsterdam"))
    return local.astimezone(UTC)
