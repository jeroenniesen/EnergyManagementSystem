"""Auth schema + store (SPEC-adjacent, slice 1 backend core): users, opaque session/access
tokens, and single-use invites. Sibling to `SettingsStore`/`HistoryStore`/`AuditStore` — same
self-healing shared-connection pattern (BACKLOG B-49 follow-up); see `ems/storage/settings.py`
for the full rationale of the connection scaffolding copied verbatim below.

This module owns the schema + connection scaffolding + `Principal`, plus user CRUD (Task 3) and
token create/resolve/revoke/list (Task 4). Onboarding/invite methods are added by Task 9 onto
this same class.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

from ems.authn import hash_token, new_token
from ems.storage.history import _log, self_healing
from ems.storage.settings import _BUSY_TIMEOUT_MS, _connection_is_dead  # reuse the shared helpers

_SESSION_TTL = timedelta(days=30)
_SESSION_REFRESH_WINDOW = timedelta(days=7)

_USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK(role IN ('reader','user','admin')),
  disabled      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  last_login_at TEXT
)
"""
_TOKENS_DDL = """
CREATE TABLE IF NOT EXISTS auth_tokens (
  id           INTEGER PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES users(id),
  token_hash   TEXT NOT NULL UNIQUE,
  kind         TEXT NOT NULL CHECK(kind IN ('session','access')),
  name         TEXT,
  created_at   TEXT NOT NULL,
  last_used_at TEXT,
  expires_at   TEXT
)
"""
_TOKENS_HASH_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash ON auth_tokens(token_hash)"
)
_TOKENS_USER_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id)"
)
_INVITES_DDL = """
CREATE TABLE IF NOT EXISTS invites (
  id          INTEGER PRIMARY KEY,
  token_hash  TEXT NOT NULL UNIQUE,
  role        TEXT NOT NULL CHECK(role IN ('reader','user','admin')),
  created_by  INTEGER REFERENCES users(id),
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  used_at     TEXT
)
"""


@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str
    role: str
    token_id: int
    kind: str  # 'session' | 'access'


@self_healing
class AuthStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._last_reheal_at: datetime | None = None

    # --- Connection scaffolding (copied verbatim from ems/storage/settings.py — sibling-store
    # pattern; see that module for the self-heal rationale). Do not change this logic here; if it
    # needs to change, change it in all sibling stores together. ---

    async def _connection(self) -> aiosqlite.Connection:
        db = self._db
        if db is not None and _connection_is_dead(db):  # proactive self-heal (see HistoryStore)
            await self._discard_connection(db, reason="worker thread stopped / connection gone")
        if self._db is None:
            async with self._connect_lock:
                if self._db is None:
                    conn = aiosqlite.connect(self.db_path)
                    conn._thread.daemon = True  # see HistoryStore._connection() for rationale
                    db = await conn
                    await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
                    self._db = db
        return self._db

    async def _note_dead_connection(self, exc: BaseException, dead_conn) -> None:
        # Called by the `self_healing` retry wrapper — see HistoryStore for the full rationale
        # (including why the discard targets the connection the failing call was using).
        await self._discard_connection(dead_conn, reason=f"{type(exc).__name__}: {exc}")

    async def reset_connection(self) -> None:
        """Force the shared connection to be discarded + reopened on the next call (best-effort)."""
        await self._discard_connection(self._db, reason="watchdog reset")

    async def _discard_connection(self, dead_conn, *, reason: str) -> None:
        # See HistoryStore._discard_connection for the sibling-race rationale (a stale caller must
        # not close a connection a sibling already healed).
        async with self._connect_lock:
            db = self._db
            if db is None:
                return
            if dead_conn is not None and db is not dead_conn:
                _log.debug("%s: shared connection already healed by a sibling (%s) — skipping "
                           "discard", type(self).__name__, reason)
                return
            self._db = None
            self._last_reheal_at = datetime.now(UTC)
            _log.warning("%s: shared connection unusable (%s) — discarding; reopening on next call",
                         type(self).__name__, reason)
            try:
                await db.close()
            except Exception:
                pass

    def reheal_stats(self) -> dict:
        at = self._last_reheal_at
        return {"last_reheal_iso": at.isoformat() if at else None}

    @asynccontextmanager
    async def _conn(self):
        yield await self._connection()

    @asynccontextmanager
    async def _write_conn(self):
        async with self._write_lock:
            yield await self._connection()

    async def close(self) -> None:
        async with self._connect_lock:
            if self._db is not None:
                await self._db.close()
                self._db = None

    def __del__(self) -> None:
        # Synchronous cleanup for a discarded-without-close() store — see HistoryStore.__del__
        # for the full rationale (avoids an indeterminate deferred file-close on aiosqlite's
        # background worker thread).
        db = self._db
        if db is None:
            return
        conn = getattr(db, "_connection", None)
        if conn is not None:
            db._connection = None
            try:
                conn.close()
            except Exception:
                pass

    async def init(self) -> None:
        async with self._write_conn() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(_USERS_DDL)
            await db.execute(_TOKENS_DDL)
            await db.execute(_TOKENS_HASH_INDEX_DDL)
            await db.execute(_TOKENS_USER_INDEX_DDL)
            await db.execute(_INVITES_DDL)
            await db.commit()

    # --- Users (Task 3) ---

    async def user_count(self) -> int:
        async with self._conn() as db:
            cur = await db.execute("SELECT COUNT(*) FROM users")
            return int((await cur.fetchone())[0])

    async def create_user(self, username: str, password_hash: str, role: str) -> int:
        async with self._write_conn() as db:
            cur = await db.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                (username, password_hash, role, datetime.now(UTC).isoformat()),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def _get_user(self, where: str, arg) -> dict | None:
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT id, username, password_hash, role, disabled FROM users WHERE {where}",
                (arg,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_user_by_username(self, username: str) -> dict | None:
        return await self._get_user("username = ? COLLATE NOCASE", username)

    async def get_user_by_id(self, user_id: int) -> dict | None:
        return await self._get_user("id = ?", user_id)

    async def set_password(self, user_id: int, password_hash: str) -> None:
        async with self._write_conn() as db:
            await db.execute(
                "UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id)
            )
            await db.commit()

    # --- Tokens (Task 4) ---

    async def create_token(self, user_id: int, kind: str, *, name: str | None = None) -> str:
        raw = new_token()
        now = datetime.now(UTC)
        expires = (now + _SESSION_TTL).isoformat() if kind == "session" else None
        async with self._write_conn() as db:
            await db.execute(
                "INSERT INTO auth_tokens (user_id, token_hash, kind, name, created_at, expires_at) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, hash_token(raw), kind, name, now.isoformat(), expires),
            )
            await db.commit()
        return raw

    async def resolve(self, raw: str) -> Principal | None:
        th = hash_token(raw)
        now = datetime.now(UTC)
        async with self._write_conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT t.id AS token_id, t.kind, t.expires_at, u.id AS user_id, "
                "u.username, u.role, u.disabled "
                "FROM auth_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?",
                (th,),
            )
            row = await cur.fetchone()
            if row is None or row["disabled"]:
                return None
            if row["expires_at"] is not None:
                exp = datetime.fromisoformat(row["expires_at"])
                if exp <= now:
                    return None
                if row["kind"] == "session" and (exp - now) < _SESSION_REFRESH_WINDOW:
                    await db.execute(
                        "UPDATE auth_tokens SET expires_at=? WHERE id=?",
                        ((now + _SESSION_TTL).isoformat(), row["token_id"]),
                    )
            await db.execute(
                "UPDATE auth_tokens SET last_used_at=? WHERE id=?",
                (now.isoformat(), row["token_id"]),
            )
            await db.commit()
            return Principal(
                user_id=row["user_id"], username=row["username"], role=row["role"],
                token_id=row["token_id"], kind=row["kind"],
            )

    async def revoke_token(self, token_id: int, user_id: int) -> bool:
        async with self._write_conn() as db:
            cur = await db.execute(
                "DELETE FROM auth_tokens WHERE id=? AND user_id=?", (token_id, user_id)
            )
            await db.commit()
            return cur.rowcount > 0

    async def list_tokens(self, user_id: int) -> list[dict]:
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, kind, name, created_at, last_used_at, expires_at "
                "FROM auth_tokens WHERE user_id=? ORDER BY created_at",
                (user_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    # --- Onboarding (Task 9) ---

    async def onboard_admin(self, username: str, password_hash: str, *,
                            migrate_token_hash: str | None) -> tuple[int, str] | None:
        """Create the first admin + its session (+ migrated access token) atomically.

        TOCTOU guard: the `COUNT(users) == 0` recheck happens INSIDE this single
        `BEGIN IMMEDIATE` transaction (not read-then-write), so two concurrent onboard calls
        can never both succeed — `BEGIN IMMEDIATE` takes the write lock up front, forcing the
        second caller's transaction to serialize behind the first and see the just-inserted row.

        Returns (user_id, session_raw), or None if any user already exists.
        """
        now = datetime.now(UTC)
        session_raw = new_token()
        async with self._write_conn() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cur = await db.execute("SELECT COUNT(*) FROM users")
                if int((await cur.fetchone())[0]) != 0:
                    await db.rollback()
                    return None
                cur = await db.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) "
                    "VALUES (?,?,?,?)",
                    (username, password_hash, "admin", now.isoformat()),
                )
                uid = int(cur.lastrowid)
                await db.execute(
                    "INSERT INTO auth_tokens (user_id, token_hash, kind, created_at, expires_at) "
                    "VALUES (?,?, 'session', ?, ?)",
                    (uid, hash_token(session_raw), now.isoformat(),
                     (now + _SESSION_TTL).isoformat()),
                )
                if migrate_token_hash:
                    await db.execute(
                        "INSERT OR IGNORE INTO auth_tokens "
                        "(user_id, token_hash, kind, name, created_at, expires_at) "
                        "VALUES (?,?, 'access', 'Migrated shared token', ?, NULL)",
                        (uid, migrate_token_hash, now.isoformat()),
                    )
                await db.commit()
                return uid, session_raw
            except Exception:
                await db.rollback()
                raise
