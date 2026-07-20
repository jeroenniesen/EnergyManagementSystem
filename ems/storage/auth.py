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
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

from ems.authn import hash_token, new_token
from ems.storage.history import _log, self_healing
from ems.storage.settings import _BUSY_TIMEOUT_MS, _connection_is_dead  # reuse the shared helpers

_SESSION_TTL = timedelta(days=30)
_SESSION_REFRESH_WINDOW = timedelta(days=7)
# Invites (Slice 2): single-use, admin-generated; public so routes/users.py can compute the same
# `expires_at` it displays without re-deriving the TTL in a second place.
INVITE_TTL = timedelta(days=7)
# `last_used_at` is best-effort telemetry (SPEC §4), NOT auth-critical — so we don't write it on
# every request. `resolve()` runs on the per-request auth hot path; an unconditional UPDATE+commit
# there turns every authenticated read into a serialized SQLite write (write-lock + fsync), which
# under concurrent load (e.g. the parallel e2e suite) queues requests until they time out. Throttle
# it: only refresh `last_used_at` when it is stale by more than this window, collapsing the common
# case to a lock-free read.
_LAST_USED_THROTTLE = timedelta(minutes=5)

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


class UsernameTaken(Exception):
    """Raised by `accept_invite` when the invite itself is valid (unused, unexpired) but the
    requested username collides with an existing account. Deliberately distinct from the
    `None` return (invalid/expired/already-used invite) so the route can tell a 409 (pick another
    username, same invite still usable — the transaction rolls back the `used_at` consume too)
    apart from a 401 (dead invite code)."""


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
                    await db.execute("PRAGMA synchronous=NORMAL")  # WAL-safe; see HistoryStore
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
                "SELECT t.id AS token_id, t.kind, t.expires_at, t.last_used_at, u.id AS user_id, "
                "u.username, u.role, u.disabled "
                "FROM auth_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?",
                (th,),
            )
            row = await cur.fetchone()
            if row is None or row["disabled"]:
                return None
            # Collect writes and commit at most once — and only when there is something to write —
            # so the common authenticated read stays a lock-free SELECT (see _LAST_USED_THROTTLE).
            dirty = False
            if row["expires_at"] is not None:
                exp = datetime.fromisoformat(row["expires_at"])
                if exp <= now:
                    return None
                if row["kind"] == "session" and (exp - now) < _SESSION_REFRESH_WINDOW:
                    await db.execute(
                        "UPDATE auth_tokens SET expires_at=? WHERE id=?",
                        ((now + _SESSION_TTL).isoformat(), row["token_id"]),
                    )
                    dirty = True
            # Best-effort telemetry, throttled off the hot path (SPEC §4): only when stale.
            lu = row["last_used_at"]
            if lu is None or (now - datetime.fromisoformat(lu)) > _LAST_USED_THROTTLE:
                await db.execute(
                    "UPDATE auth_tokens SET last_used_at=? WHERE id=?",
                    (now.isoformat(), row["token_id"]),
                )
                dirty = True
            if dirty:
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

    async def replace_token(self, user_id: int, name: str) -> str:
        """Atomic revoke-and-remint of `user_id`'s `access` token(s) named `name` — the contract
        `POST /api/auth/tokens {replace: true}` exposes and the iOS widget relies on (design §5/§7):
        a per-device token that is safe to re-request on every login without ever accumulating
        duplicates or leaving a stale/leaked one alive. ONE `BEGIN IMMEDIATE` transaction: DELETE
        every access token this user owns with this exact name, INSERT the replacement (hash
        only), commit; on any exception, rollback and re-raise (mirrors `onboard_admin` /
        `set_role` / `accept_invite`).

        Concurrency semantics (deliberately chosen — see
        test_replace_token_concurrent_yields_exactly_one_survivor): two callers racing
        `replace_token(user_id, name)` each get back their OWN raw token, but only the one whose
        transaction COMMITS LAST resolves. Serialization is enforced at TWO layers that agree: the
        store's `_write_lock` (`_write_conn`) serializes DELETE→INSERT→commit within THIS process so
        the two transactions never interleave on the shared connection, and `BEGIN IMMEDIATE` takes
        SQLite's write lock so a separate process/connection can't slip between them either. Every
        transaction's DELETE removes ANY row with that (user_id, name) — including a row a
        just-committed sibling call inserted — before inserting its own row, so that ordering (the
        same guarantee `onboard_admin`/`accept_invite` lean on) makes the LAST commit's INSERT the
        one still standing once every caller has finished. This is "last write wins", not "first
        writer wins" and not "both survive": after any number of concurrent replaces, exactly one
        `auth_tokens` row named `name` exists for this user, and exactly one of the raws handed
        back is ever valid — never zero, never more than one.
        """
        raw = new_token()
        now = datetime.now(UTC)
        async with self._write_conn() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    "DELETE FROM auth_tokens WHERE user_id=? AND name=? AND kind='access'",
                    (user_id, name),
                )
                await db.execute(
                    "INSERT INTO auth_tokens (user_id, token_hash, kind, name, created_at, "
                    "expires_at) VALUES (?,?, 'access', ?, ?, NULL)",
                    (user_id, hash_token(raw), name, now.isoformat()),
                )
                await db.commit()
                return raw
            except BaseException:
                await db.rollback()
                raise

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
            except BaseException:
                # BaseException (not just Exception): a cancellation (asyncio.CancelledError)
                # must still roll back the open BEGIN IMMEDIATE transaction on the shared write
                # connection, or it leaks into the next caller (review hardening fix).
                await db.rollback()
                raise

    # --- User management + invites (Slice 2) ---

    async def list_users(self) -> list[dict]:
        """Never includes `password_hash` — this feeds the admin user-management list."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, username, role, disabled, created_at, last_login_at "
                "FROM users ORDER BY id"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def _other_enabled_admin_count(self, db, exclude_user_id: int) -> int:
        cur = await db.execute(
            "SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0 AND id != ?",
            (exclude_user_id,),
        )
        return int((await cur.fetchone())[0])

    async def set_role(self, user_id: int, role: str, *, actor_id: int) -> bool:
        """Change a user's role. The last-admin + self-demote guards are enforced INSIDE one
        `BEGIN IMMEDIATE` transaction with the condition rechecked in the transaction (never
        read-then-write — SPEC §6 TOCTOU rule), mirroring `onboard_admin`. Returns False
        (rolled back, no-op) if the user doesn't exist, if this is a self-demotion out of admin,
        or if it would leave zero *other* enabled admins. Does not itself validate `role` is one
        of the known roles — callers validate before calling (the CHECK constraint is the last
        line of defense and would raise `sqlite3.IntegrityError` for a bogus value)."""
        async with self._write_conn() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT role FROM users WHERE id=?", (user_id,))
                row = await cur.fetchone()
                if row is None:
                    await db.rollback()
                    return False
                demoting_from_admin = row["role"] == "admin" and role != "admin"
                if demoting_from_admin:
                    if actor_id == user_id:
                        await db.rollback()
                        return False
                    if await self._other_enabled_admin_count(db, user_id) < 1:
                        await db.rollback()
                        return False
                await db.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
                await db.commit()
                return True
            except BaseException:
                await db.rollback()
                raise

    async def set_disabled(self, user_id: int, disabled: bool, *, actor_id: int) -> bool:
        """Enable/disable a user. Same transactional last-admin + self-disable guard as
        `set_role` (only when actually disabling — re-enabling never needs it). When disabling,
        the user's `auth_tokens` are deleted in the SAME transaction, so their sessions die
        immediately rather than lingering until natural expiry. Returns False (rolled back,
        no-op) for an unknown user or a blocked guard."""
        async with self._write_conn() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT role, disabled FROM users WHERE id=?", (user_id,))
                row = await cur.fetchone()
                if row is None:
                    await db.rollback()
                    return False
                if disabled:
                    if actor_id == user_id:
                        await db.rollback()
                        return False
                    already_disabled = bool(row["disabled"])
                    if row["role"] == "admin" and not already_disabled:
                        if await self._other_enabled_admin_count(db, user_id) < 1:
                            await db.rollback()
                            return False
                await db.execute(
                    "UPDATE users SET disabled=? WHERE id=?", (1 if disabled else 0, user_id)
                )
                if disabled:
                    await db.execute("DELETE FROM auth_tokens WHERE user_id=?", (user_id,))
                await db.commit()
                return True
            except BaseException:
                await db.rollback()
                raise

    async def create_invite(self, role: str, *, created_by: int) -> str:
        """Create a single-use invite for `role`, returning the RAW code (only its sha256 is
        stored — same convention as tokens)."""
        raw = new_token()
        now = datetime.now(UTC)
        async with self._write_conn() as db:
            await db.execute(
                "INSERT INTO invites (token_hash, role, created_by, created_at, expires_at) "
                "VALUES (?,?,?,?,?)",
                (hash_token(raw), role, created_by, now.isoformat(),
                 (now + INVITE_TTL).isoformat()),
            )
            await db.commit()
        return raw

    async def list_invites(self) -> list[dict]:
        """Never includes `token_hash` — only the admin-facing metadata."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, role, created_by, created_at, expires_at, used_at "
                "FROM invites ORDER BY created_at DESC"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def revoke_invite(self, invite_id: int) -> bool:
        async with self._write_conn() as db:
            cur = await db.execute("DELETE FROM invites WHERE id=?", (invite_id,))
            await db.commit()
            return cur.rowcount > 0

    async def accept_invite(
        self, raw_code: str, username: str, password_hash: str
    ) -> tuple[int, str] | None:
        """Atomic single-use consume + user create + session mint, mirroring `onboard_admin`'s
        structure. The consume is a single `UPDATE ... WHERE token_hash=? AND used_at IS NULL
        AND expires_at > ?` requiring `rowcount == 1` (never a read-then-write check — the same
        TOCTOU-safe pattern as onboarding), inside one `BEGIN IMMEDIATE` that also creates the
        user (with the INVITE's role, not caller-supplied) and their session. Two concurrent
        `accept_invite` calls for the same code can consume it at most once.

        Returns `(user_id, session_raw)`, or `None` if the code is unknown/expired/already used.
        Raises `UsernameTaken` (after rolling back — the invite is NOT burned) if the invite is
        valid but `username` collides with an existing account, so the caller can retry the
        same invite with a different username."""
        now = datetime.now(UTC)
        th = hash_token(raw_code)
        session_raw = new_token()
        async with self._write_conn() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cur = await db.execute(
                    "UPDATE invites SET used_at=? WHERE token_hash=? AND used_at IS NULL "
                    "AND expires_at > ?",
                    (now.isoformat(), th, now.isoformat()),
                )
                if cur.rowcount != 1:
                    await db.rollback()
                    return None
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT role FROM invites WHERE token_hash=?", (th,))
                role = (await cur.fetchone())["role"]
                cur = await db.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) "
                    "VALUES (?,?,?,?)",
                    (username, password_hash, role, now.isoformat()),
                )
                uid = int(cur.lastrowid)
                await db.execute(
                    "INSERT INTO auth_tokens (user_id, token_hash, kind, created_at, expires_at) "
                    "VALUES (?,?, 'session', ?, ?)",
                    (uid, hash_token(session_raw), now.isoformat(),
                     (now + _SESSION_TTL).isoformat()),
                )
                await db.commit()
                return uid, session_raw
            except sqlite3.IntegrityError:
                # UNIQUE COLLATE NOCASE on users.username — the ONLY constraint this transaction
                # can hit. Roll back (un-consumes the invite) and let the caller retry.
                await db.rollback()
                raise UsernameTaken(username) from None
            except BaseException:
                await db.rollback()
                raise
