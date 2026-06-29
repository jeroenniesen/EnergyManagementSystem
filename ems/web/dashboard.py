from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

DASHBOARD_API_VERSION = 1
DASHBOARD_CACHE_TTL_SECONDS = 10


def degraded_section(message: str, now: datetime) -> dict[str, Any]:
    return {
        "state": "degraded",
        "message": message,
        "updated_at": now.isoformat(),
    }


class DashboardSnapshotCache:
    def __init__(
        self,
        ttl_seconds: int = DASHBOARD_CACHE_TTL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] | None = None
        self._at: datetime | None = None

    def get_or_build(self, build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        now = self._clock()
        if self._fresh(now):
            return dict(self._snapshot or {})
        with self._lock:
            now = self._clock()
            if self._fresh(now):
                return dict(self._snapshot or {})
            snapshot = build()
            self._snapshot = snapshot
            self._at = now
            return dict(snapshot)

    def _fresh(self, now: datetime) -> bool:
        return (
            self._snapshot is not None
            and self._at is not None
            and (now - self._at).total_seconds() < self.ttl_seconds
        )


def dashboard_shell(now: datetime, server_name: str = "Home EMS") -> dict[str, Any]:
    stamp = now.isoformat()
    return {
        "api_version": DASHBOARD_API_VERSION,
        "generated_at": stamp,
        "server_time": stamp,
        "server_name": server_name or "Home EMS",
        "cache_ttl_seconds": DASHBOARD_CACHE_TTL_SECONDS,
        "degraded_sections": [],
    }
