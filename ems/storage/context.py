"""Shared persistence boundary for the application's SQLite repositories."""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any

from ems.storage.audit import AuditStore
from ems.storage.auth import AuthStore
from ems.storage.cache import CacheStore
from ems.storage.control_state import ControlStateStore
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore

logger = logging.getLogger(__name__)


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
    _closed_stores: set[str] | None = None
    close_errors: tuple[str, ...] = ()

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
        """Close every present store, isolating failures and allowing retries.

        Repositories may expose either synchronous or asynchronous ``close`` hooks.  A failed
        hook is logged and retained for a subsequent call; successful stores are never closed
        twice.  Shutdown therefore remains best-effort while still giving callers a chance to
        retry transient failures.
        """
        if self._closed:
            return
        if self._closed_stores is None:
            self._closed_stores = set()
        errors: list[str] = []
        stores = (
            ("history", self.history),
            ("settings", self.settings),
            ("override", self.override),
            ("audit", self.audit),
            ("auth", self.auth),
            ("cache", self.cache),
            ("control_state", self.control_state),
        )
        for name, store in stores:
            if store is None or name in self._closed_stores:
                continue
            hook = getattr(store, "close", None)
            if hook is None:
                self._closed_stores.add(name)
                continue
            try:
                result = hook()
                if inspect.isawaitable(result):
                    await result
                self._closed_stores.add(name)
            except Exception as exc:  # shutdown must not strand later repositories
                detail = f"{name}: {exc!r}"
                errors.append(detail)
                logger.warning("failed to close storage repository %s", name, exc_info=True)
        self.close_errors = tuple(errors)
        if all(store is None or name in self._closed_stores for name, store in stores):
            self._closed = True
