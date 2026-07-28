"""Shared persistence boundary for the application's SQLite repositories."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ems.storage.audit import AuditStore
from ems.storage.auth import AuthStore
from ems.storage.cache import CacheStore
from ems.storage.control_state import ControlStateStore
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore


@dataclass
class StorageContext:
    """The repositories used by one application instance.

    Stores are optional because read-only and hermetic deployments do not necessarily configure
    every persistence feature.  ``close`` is asynchronous to match the long-lived stores while
    also handling the synchronous cache/control-state stores.
    """

    history: HistoryStore | None = None
    settings: SettingsStore | None = None
    override: SettingsStore | None = None
    audit: AuditStore | None = None
    auth: AuthStore | None = None
    cache: CacheStore | None = None
    control_state: ControlStateStore | None = None
    _closed: bool = False

    @classmethod
    def from_existing(cls, **stores: Any) -> StorageContext:
        """Build a context from already-created stores (especially useful in tests)."""
        allowed = {"history", "settings", "override", "audit", "auth", "cache", "control_state"}
        unknown = set(stores) - allowed
        if unknown:
            raise TypeError(f"unknown storage(s): {', '.join(sorted(unknown))}")
        return cls(**stores)

    @classmethod
    def create(cls, db_path: str, *, access_token_idle_days: int = 90) -> StorageContext:
        """Construct the current repository set against ``db_path``."""
        return cls(
            history=HistoryStore(db_path),
            settings=SettingsStore(db_path),
            override=SettingsStore(db_path, table="overrides"),
            audit=AuditStore(db_path),
            auth=AuthStore(db_path, access_token_idle_days=access_token_idle_days),
            cache=CacheStore(db_path),
            control_state=ControlStateStore(db_path),
        )

    async def close(self) -> None:
        """Close every present store once; repeated calls are harmless."""
        if self._closed:
            return
        self._closed = True
        for store in (self.history, self.settings, self.override, self.audit, self.auth):
            if store is not None:
                await store.close()
