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

_BUSY_TIMEOUT_MS = 5000  # see ems/storage/history.py for the WAL/synchronous/timeout rationale
_KEY = "controller"


class _CorruptControlState:
    """Sentinel returned by `load()` when persisted control state EXISTS but cannot be read — a
    row whose JSON is corrupt/unparseable, or a genuine read failure. It is DISTINCT from `{}`
    (genuinely nothing persisted — a fresh boot), so the caller can fail SAFE: the last battery
    command's device state is UNKNOWN, which must BLOCK a refuse-when-busy restart (I2). Collapsing
    both cases to `{}` (the old behaviour) let a corrupt blob fail OPEN. Singleton; identity-checked
    via `is CONTROL_STATE_CORRUPT`."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "CONTROL_STATE_CORRUPT"


CONTROL_STATE_CORRUPT = _CorruptControlState()


class ControlStateStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_MS / 1000)
        con.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        con.execute("PRAGMA synchronous=NORMAL")  # WAL-safe; see HistoryStore
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

    def load(self) -> dict | _CorruptControlState:
        """The persisted state (never raises), distinguishing THREE cases the fail-safe restart
        gate depends on:
        - `{}`  — genuinely NO persisted state: no row, or the table doesn't exist yet (a fresh
          boot that never wrote). The caller keeps the clean default (restart permitted).
        - `CONTROL_STATE_CORRUPT` — a row EXISTS but its JSON is corrupt/unparseable, OR the read
          itself fails (I/O, malformed DB). We CANNOT prove the last command confirmed, so the
          caller must fail SAFE (block the restart). This must NOT collapse to `{}`.
        - a `dict` — a well-formed persisted snapshot, restored as-is.
        """
        with timed("store.control_state.read"):
            try:
                con = self._conn()
                try:
                    row = con.execute(
                        "SELECT value FROM control_state WHERE key=?", (_KEY,)
                    ).fetchone()
                finally:
                    con.close()
            except sqlite3.OperationalError as e:
                # A not-yet-created table is genuinely "nothing persisted" (fresh boot) → {}. Any
                # OTHER operational error is a real read failure we can't interpret as absence.
                if "no such table" in str(e).lower():
                    return {}
                return CONTROL_STATE_CORRUPT
            except sqlite3.Error:
                # A genuine read failure (I/O, malformed DB, locked-out, ...): we cannot prove the
                # state is absent, so signal corrupt/unknown and let the caller fail safe.
                return CONTROL_STATE_CORRUPT
            if not row:
                return {}  # table exists but no row → genuinely fresh, nothing persisted
            try:
                return json.loads(row[0])
            except (ValueError, TypeError):
                # A row EXISTS but its stored value is corrupt/unparseable JSON: persisted state is
                # present but unreadable, so corrupt/unknown — NOT a fresh {} (the old fail-open).
                return CONTROL_STATE_CORRUPT

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
