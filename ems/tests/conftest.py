"""Shared pytest helpers for API app construction."""

# Boot-time maintenance purges samples/notifications older than `history_retention_days`
# (default 90). Tests that seed fixed past dates must disable that purge so TestClient
# lifespan does not erase the fixture data when the wall clock moves forward — while
# keeping the default retention value so year-view labeling ("last 90 days") stays true.
NO_HISTORY_PURGE = {"history_purge_enabled": False, "history_backup_keep": 0}

# `_seed()` in test_export_package uses 2026-06-28; the package endpoint's default `days=90`
# window drifts with the wall clock — request a wide window in those tests.
EXPORT_PACKAGE_WIDE_DAYS = 400
