"""Persisted runtime control state (SPEC §13.3 / energy review #5).

The mode controller's safety counters — switches used today, last switch time, the daily counter
date, the last requested/confirmed action, and the original vendor mode — must survive a restart.
Otherwise a reboot resets dwell + the daily switch cap, which is exactly when a control system is
most likely to do something surprising.

Deliberately **sync** (plain sqlite3): the control tick runs off the event loop (asyncio.to_thread),
so it can't await an aiosqlite store. A single JSON blob under one key in the shared DB.
"""
from __future__ import annotations

import json
import sqlite3

from ems.perf import timed

_BUSY_TIMEOUT_MS = 3000
_KEY = "controller"


class ControlStateStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_MS / 1000)
        con.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        return con

    def init(self) -> None:
        con = self._conn()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                "CREATE TABLE IF NOT EXISTS control_state "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            con.commit()
        finally:
            con.close()

    def load(self) -> dict:
        """The persisted state dict, or {} if none/unparseable (never raises)."""
        with timed("store.control_state.read"):
            try:
                con = self._conn()
                try:
                    row = con.execute(
                        "SELECT value FROM control_state WHERE key=?", (_KEY,)
                    ).fetchone()
                finally:
                    con.close()
                return json.loads(row[0]) if row else {}
            except (sqlite3.Error, ValueError, TypeError):
                return {}

    def save(self, state: dict) -> None:
        with timed("store.control_state.write"):
            con = self._conn()
            try:
                con.execute(
                    "INSERT INTO control_state (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (_KEY, json.dumps(state)),
                )
                con.commit()
            finally:
                con.close()
