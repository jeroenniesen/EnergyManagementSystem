# Auth Slice 1 — Backend Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single shared bearer token with real username/password identities, three roles (reader/user/admin), opaque bearer session + access tokens, forced first-admin onboarding, and identity-based authorization — the foundation the other slices build on.

**Architecture:** A new `AuthStore` (aiosqlite, sibling-store pattern) owns `users`/`auth_tokens`/`invites` and resolves an opaque bearer token to a `Principal{user_id, username, role, token_id, kind}`. Authorization tiers (VIEW/OPERATE/ADMIN) live in `ems/web/authz.py`. The existing **pure-ASGI** `_AccessMiddleware` keeps its shape and its origin-first CSRF gate; only its token gate is rewritten to resolve a principal and enforce tier + session-kind + forced-onboarding. Auth endpoints live in a new `ems/web/routes/auth.py`.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, `argon2-cffi` (new), Starlette `TestClient`, pytest (sync + `asyncio.run`), React/Vite + Playwright (frontend).

## Global Constraints

- Python `>=3.12`; deps declared in `pyproject.toml` as `"pkg>=X.Y"`. New runtime dep: `argon2-cffi>=23.1`.
- The auth gate MUST remain a **pure-ASGI** middleware (`_AccessMiddleware`) — never `@app.middleware`/`BaseHTTPMiddleware` (it starves the override control cycle).
- CSRF stays **origin-header based**; **no cookies** are introduced.
- Tokens are stored **only** as `sha256(raw)`; passwords hashed with **Argon2id** (`argon2-cffi`).
- Every mutating route must resolve to an OPERATE/ADMIN tier or be in the exempt set — the invariant test enforces this (updated in Task 7).
- Tests are **synchronous**: drive async code with `asyncio.run(...)`; hit routes via `TestClient(app)` as a context manager (runs the lifespan). No `pytest-asyncio`.
- Run tests: `uv run pytest ems/tests`. Lint: `uv run ruff check ems`.
- Commit after each task.

## File Structure

- Create `ems/authn.py` — pure password + token crypto (no I/O).
- Create `ems/storage/auth.py` — `AuthStore` + `Principal` (schema, users, tokens, onboarding).
- Create `ems/web/authz.py` — `Tier`, `role_satisfies`, `required_tier`, `requires_session`.
- Create `ems/web/routes/auth.py` — `build_router(ctx)`: discovery/login/logout/me/password/onboard.
- Create `ems/web/frontend/src/Login.tsx`, `ems/web/frontend/src/Onboarding.tsx`.
- Modify `pyproject.toml` (dep), `ems/main.py` (construct + pass store), `ems/web/context.py` (AppContext field), `ems/web/api.py` (create_app kwarg, lifespan init, AppContext build, middleware gate, router include, path/exempt sets, invariant test), `ems/web/frontend/src/App.tsx` (auth gate), `ems/web/frontend/src/auth.ts` (`clearToken`), `ems/web/frontend/src/Settings.tsx` (retire paste-token box, add logout).
- Tests: `ems/tests/test_authn.py`, `test_auth_store.py`, `test_authz.py`, `test_auth_api.py`, `test_auth_onboarding.py`; extend `ems/web/frontend/e2e/auth.spec.ts`.

---

### Task 1: Password & token crypto (`ems/authn.py`)

**Files:**
- Modify: `pyproject.toml` (add dep)
- Create: `ems/authn.py`
- Test: `ems/tests/test_authn.py`

**Interfaces:**
- Produces: `hash_password(password: str) -> str`, `verify_password(encoded: str, password: str) -> bool`, `dummy_verify() -> None`, `new_token() -> str`, `hash_token(raw: str) -> str`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to the `[project].dependencies` list (after `"httpx>=0.27",`):
```toml
    "argon2-cffi>=23.1",
```
Then run `uv sync` so the lockfile updates:
Run: `uv sync`
Expected: resolves and installs `argon2-cffi`.

- [ ] **Step 2: Write the failing test**

Create `ems/tests/test_authn.py`:
```python
from ems.authn import hash_password, verify_password, new_token, hash_token, dummy_verify


def test_password_hash_roundtrip():
    h = hash_password("correct horse")
    assert h != "correct horse"
    assert h.startswith("$argon2")
    assert verify_password(h, "correct horse") is True
    assert verify_password(h, "wrong") is False


def test_verify_bad_hash_returns_false_not_raise():
    assert verify_password("not-a-hash", "x") is False


def test_dummy_verify_does_not_raise():
    dummy_verify()


def test_tokens_random_and_hash_stable():
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 32
    assert hash_token(a) == hash_token(a)
    assert hash_token(a) != hash_token(b)
    assert len(hash_token(a)) == 64  # sha256 hex
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest ems/tests/test_authn.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ems.authn'`.

- [ ] **Step 4: Implement `ems/authn.py`**

```python
from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id (argon2-cffi default). Params bounded for a Raspberry Pi 5.
_ph = PasswordHasher(time_cost=2, memory_cost=64 * 1024, parallelism=2)
# Precomputed hash so the missing-user login path does equal work (no timing oracle).
_DUMMY_HASH = _ph.hash("dummy-password-for-timing-equalization")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return _ph.verify(encoded, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def dummy_verify() -> None:
    """Constant-time-ish work for the user-not-found path."""
    try:
        _ph.verify(_DUMMY_HASH, "definitely-wrong")
    except Exception:
        pass


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256-bit


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_authn.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock ems/authn.py ems/tests/test_authn.py
git commit -m "feat(auth): Argon2id password + opaque token crypto helpers"
```

---

### Task 2: AuthStore schema + init (`ems/storage/auth.py`)

**Files:**
- Create: `ems/storage/auth.py`
- Test: `ems/tests/test_auth_store.py`

**Interfaces:**
- Consumes: `self_healing` from `ems.storage.history`.
- Produces: `class AuthStore(db_path: str)` with `async init()`, `async close()`; module-level DDL constants; `@dataclass(frozen=True) class Principal(user_id:int, username:str, role:str, token_id:int, kind:str)`.

- [ ] **Step 1: Write the failing test**

Create `ems/tests/test_auth_store.py`:
```python
import asyncio
import sqlite3

from ems.storage.auth import AuthStore


def _tables(db_path: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def test_init_creates_tables_idempotently(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    store = AuthStore(db)

    async def run():
        await store.init()
        await store.init()  # must be idempotent
        await store.close()

    asyncio.run(run())
    assert {"users", "auth_tokens", "invites"} <= _tables(db)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest ems/tests/test_auth_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ems.storage.auth'`.

- [ ] **Step 3: Create `ems/storage/auth.py` with the connection scaffolding**

Copy the connection scaffolding **verbatim** from `ems/storage/settings.py` lines 37–112 — the methods `_connection`, `_conn`, `_write_conn`, `close`, `__del__`, `_note_dead_connection`, `reset_connection`, `_discard_connection`, `reheal_stats` — into the new class body. Do not change their logic. Then add the auth-specific head:

```python
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

from ems.authn import hash_token, new_token
from ems.storage.history import self_healing
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
_TOKENS_HASH_INDEX_DDL = "CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash ON auth_tokens(token_hash)"
_TOKENS_USER_INDEX_DDL = "CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id)"
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

    # <-- paste settings.py:37-112 connection helpers here (rename none; they read self.db_path) -->

    async def init(self) -> None:
        async with self._write_conn() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(_USERS_DDL)
            await db.execute(_TOKENS_DDL)
            await db.execute(_TOKENS_HASH_INDEX_DDL)
            await db.execute(_TOKENS_USER_INDEX_DDL)
            await db.execute(_INVITES_DDL)
            await db.commit()
```

Note: if `_BUSY_TIMEOUT_MS`/`_connection_is_dead` are not exported from `settings.py`, copy their definitions too (they sit near the top of `settings.py`). Verify the imports resolve before running.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ems/storage/auth.py ems/tests/test_auth_store.py
git commit -m "feat(auth): AuthStore schema (users/tokens/invites) + init"
```

---

### Task 3: AuthStore user methods

**Files:**
- Modify: `ems/storage/auth.py`
- Test: `ems/tests/test_auth_store.py`

**Interfaces:**
- Produces: `async user_count() -> int`, `async create_user(username, password_hash, role) -> int`, `async get_user_by_username(username) -> dict | None`, `async get_user_by_id(user_id) -> dict | None`, `async set_password(user_id, password_hash) -> None`. Row dicts carry `id, username, password_hash, role, disabled`.

- [ ] **Step 1: Write the failing test** (append to `test_auth_store.py`)

```python
def test_user_crud_and_case_insensitive_unique(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        assert await s.user_count() == 0
        uid = await s.create_user("Alice", "hash1", "admin")
        assert await s.user_count() == 1
        u = await s.get_user_by_username("alice")  # COLLATE NOCASE
        assert u["id"] == uid and u["role"] == "admin"
        assert (await s.get_user_by_id(uid))["username"] == "Alice"
        dup = False
        try:
            await s.create_user("ALICE", "h", "user")
        except Exception:
            dup = True
        assert dup
        await s.set_password(uid, "hash2")
        assert (await s.get_user_by_username("alice"))["password_hash"] == "hash2"

    asyncio.run(run())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest ems/tests/test_auth_store.py::test_user_crud_and_case_insensitive_unique -q`
Expected: FAIL — `AttributeError: 'AuthStore' object has no attribute 'user_count'`.

- [ ] **Step 3: Add the methods** (in `AuthStore`)

```python
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
            await db.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
            await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ems/storage/auth.py ems/tests/test_auth_store.py
git commit -m "feat(auth): AuthStore user create/get/count/set-password"
```

---

### Task 4: AuthStore token methods (create / resolve / revoke / list)

**Files:**
- Modify: `ems/storage/auth.py`
- Test: `ems/tests/test_auth_store.py`

**Interfaces:**
- Produces: `async create_token(user_id, kind, *, name=None) -> str` (returns raw), `async resolve(raw) -> Principal | None`, `async revoke_token(token_id, user_id) -> bool` (owner-scoped), `async list_tokens(user_id) -> list[dict]`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_token_create_resolve_revoke(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "user")
        raw = await s.create_token(uid, "session")
        p = await s.resolve(raw)
        assert p.user_id == uid and p.role == "user" and p.kind == "session"
        assert await s.resolve("nope") is None
        # owner-scoped: a different user id cannot revoke it
        assert await s.revoke_token(p.token_id, uid + 999) is False
        assert await s.resolve(raw) is not None
        assert await s.revoke_token(p.token_id, uid) is True
        assert await s.resolve(raw) is None

    asyncio.run(run())


def test_disabled_user_token_and_sliding_refresh(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "user")
        # a session already inside the 7-day refresh window
        from datetime import UTC, datetime, timedelta
        from ems.authn import hash_token, new_token
        raw = new_token()
        soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        async with s._write_conn() as db:
            await db.execute(
                "INSERT INTO auth_tokens (user_id, token_hash, kind, created_at, expires_at) "
                "VALUES (?,?, 'session', ?, ?)",
                (uid, hash_token(raw), datetime.now(UTC).isoformat(), soon),
            )
            await db.commit()
        assert await s.resolve(raw) is not None  # bumps expiry
        async with s._conn() as db:
            cur = await db.execute("SELECT expires_at FROM auth_tokens WHERE user_id=?", (uid,))
            new_exp = datetime.fromisoformat((await cur.fetchone())[0])
        assert new_exp > datetime.now(UTC) + timedelta(days=20)  # slid to ~30d
        # disabling the user rejects the token
        async with s._write_conn() as db:
            await db.execute("UPDATE users SET disabled=1 WHERE id=?", (uid,))
            await db.commit()
        assert await s.resolve(raw) is None

    asyncio.run(run())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest ems/tests/test_auth_store.py -q`
Expected: FAIL — `AttributeError: 'AuthStore' object has no attribute 'create_token'`.

- [ ] **Step 3: Add the methods** (in `AuthStore`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ems/storage/auth.py ems/tests/test_auth_store.py
git commit -m "feat(auth): AuthStore token create/resolve/revoke/list + sliding refresh"
```

---

### Task 5: Authorization tiers (`ems/web/authz.py`)

**Files:**
- Create: `ems/web/authz.py`
- Test: `ems/tests/test_authz.py`

**Interfaces:**
- Produces: `class Tier(IntEnum){VIEW,OPERATE,ADMIN}`, `role_satisfies(role, tier) -> bool`, `required_tier(path, method) -> Tier`, `requires_session(path) -> bool`, `OPERATE_PATHS: frozenset[str]`, `EXEMPT_PATHS: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

Create `ems/tests/test_authz.py`:
```python
from ems.web.authz import Tier, required_tier, requires_session, role_satisfies


def test_role_satisfies():
    assert role_satisfies("reader", Tier.VIEW)
    assert not role_satisfies("reader", Tier.OPERATE)
    assert role_satisfies("user", Tier.OPERATE)
    assert not role_satisfies("user", Tier.ADMIN)
    assert role_satisfies("admin", Tier.ADMIN)
    assert not role_satisfies("bogus", Tier.VIEW)


def test_required_tier():
    assert required_tier("/api/status", "GET") == Tier.VIEW
    assert required_tier("/api/settings", "POST") == Tier.OPERATE
    assert required_tier("/api/users", "GET") == Tier.ADMIN
    assert required_tier("/api/users/5", "DELETE") == Tier.ADMIN
    assert required_tier("/api/invites", "POST") == Tier.ADMIN


def test_requires_session():
    assert requires_session("/api/auth/password")
    assert requires_session("/api/auth/logout")
    assert requires_session("/api/auth/tokens")
    assert requires_session("/api/auth/tokens/3")
    assert not requires_session("/api/settings")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest ems/tests/test_authz.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ems.web.authz'`.

- [ ] **Step 3: Implement `ems/web/authz.py`**

```python
from __future__ import annotations

from enum import IntEnum


class Tier(IntEnum):
    VIEW = 0
    OPERATE = 1
    ADMIN = 2


_ROLE_RANK = {"reader": 0, "user": 1, "admin": 2}

# Writes any 'user' may perform (the old _WRITE_API_PATHS set).
OPERATE_PATHS = frozenset({
    "/api/override",
    "/api/settings",
    "/api/ai/validate",
    "/api/chat",
    "/api/car/soc",
    "/api/notifications/read",
})
# Admin-only surfaces (prefix match).
_ADMIN_PREFIXES = ("/api/users", "/api/invites")
# Interactive-session-only surfaces (kind == 'session'); no access/machine token allowed.
_SESSION_ONLY_PATHS = frozenset({"/api/auth/password", "/api/auth/logout"})
_SESSION_ONLY_PREFIXES = ("/api/auth/tokens",)
# Reachable without any auth (login/onboard/discovery/invite-accept).
EXEMPT_PATHS = frozenset({
    "/api/auth",
    "/api/auth/login",
    "/api/auth/onboard",
})


def role_satisfies(role: str, tier: Tier) -> bool:
    return _ROLE_RANK.get(role, -1) >= int(tier)


def required_tier(path: str, method: str) -> Tier:
    if path.startswith(_ADMIN_PREFIXES):
        return Tier.ADMIN
    if path in OPERATE_PATHS:
        return Tier.OPERATE
    return Tier.VIEW


def requires_session(path: str) -> bool:
    return path in _SESSION_ONLY_PATHS or path.startswith(_SESSION_ONLY_PREFIXES)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_authz.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ems/web/authz.py ems/tests/test_authz.py
git commit -m "feat(auth): permission tiers (VIEW/OPERATE/ADMIN) + session-only rules"
```

---

### Task 6: Wire AuthStore into the app (no behavior change yet)

**Files:**
- Modify: `ems/web/context.py`, `ems/web/api.py`, `ems/main.py`
- Test: `ems/tests/test_auth_api.py`

**Interfaces:**
- Consumes: `AuthStore` (Task 2).
- Produces: `AppContext.auth_store: AuthStore | None`; `create_app(..., auth_store=None)`; `auth_store.init()` awaited in the lifespan; `app.state.users_exist: bool`.

- [ ] **Step 1: Write the failing test**

Create `ems/tests/test_auth_api.py`:
```python
import asyncio

from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.storage.auth import AuthStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


def _app(db: str, *, token: str | None = None):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(db),
        auth_store=AuthStore(db),
        web_auth_token=token,
    )


def test_app_boots_with_auth_store_and_users_exist_flag(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    app = _app(db)
    with TestClient(app) as c:
        assert app.state.users_exist is False  # fresh DB → no users
        assert c.get("/api/auth").status_code == 200
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest ems/tests/test_auth_api.py -q`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'auth_store'`.

- [ ] **Step 3: Add the AppContext field** (`ems/web/context.py`)

Add the import near the other storage imports (~L32):
```python
from ems.storage.auth import AuthStore
```
Add the field in `AppContext`, right after `audit_store` (~L64):
```python
    auth_store: AuthStore | None
```

- [ ] **Step 4: Thread it through `create_app`** (`ems/web/api.py`)

Add the kwarg to the `create_app` signature next to `audit_store` (~L746):
```python
    auth_store: AuthStore | None = None,
```
Add the import near the storage imports at the top of `api.py`:
```python
from ems.storage.auth import AuthStore
```
Pass it into the `AppContext(...)` construction (~L3419), next to `audit_store=audit_store,`:
```python
        auth_store=auth_store,
```
In the lifespan (~L1138, right after the `audit_store` init), add the init **and** the flag:
```python
        if auth_store is not None:
            await auth_store.init()
            _app.state.users_exist = (await auth_store.user_count()) > 0
        else:
            _app.state.users_exist = True  # no auth store → legacy behaviour, nothing to onboard
```

- [ ] **Step 5: Construct the store in `main.py`** (`ems/main.py`)

Add the import near the store imports, then in `build_app()` beside the other stores (~L36):
```python
    from ems.storage.auth import AuthStore  # or add to the top import block
    auth_store = AuthStore(str(db_path))
```
And pass it into the `create_app(...)` call (beside `audit_store=audit_store,`):
```python
        auth_store=auth_store,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_api.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ems/web/context.py ems/web/api.py ems/main.py ems/tests/test_auth_api.py
git commit -m "feat(auth): construct + init AuthStore, expose app.state.users_exist"
```

---

### Task 7: Identity-based middleware gate + forced onboarding

**Files:**
- Modify: `ems/web/api.py` (the `_AccessMiddleware` token gate, path sets, the invariant test if it lives in tests)
- Test: `ems/tests/test_auth_api.py`

**Interfaces:**
- Consumes: `required_tier`, `role_satisfies`, `requires_session`, `EXEMPT_PATHS`, `OPERATE_PATHS` (Task 5); `auth_store.resolve` (Task 4); `app.state.users_exist` (Task 6).
- Produces: `scope["auth_principal"]` set for authorized `/api/` requests; `403` for wrong role / access-token-on-session-path; `409 {"detail":"onboarding_required"}` while no users exist.

- [ ] **Step 1: Write the failing tests** (append to `test_auth_api.py`)

```python
def _seed_user(db: str, username: str, password: str, role: str):
    from ems.authn import hash_password
    s = AuthStore(db)

    async def run():
        await s.init()
        await s.create_user(username, hash_password(password), role)
        await s.close()

    asyncio.run(run())


def test_forced_onboarding_blocks_until_admin(tmp_path):
    db = str(tmp_path / "ems.sqlite")  # no users
    with TestClient(_app(db)) as c:
        r = c.get("/api/status")
        assert r.status_code == 409 and r.json()["detail"] == "onboarding_required"


def test_reader_forbidden_on_operate_but_can_view(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "rdr", "pw12345678", "reader")
    with TestClient(_app(db)) as c:
        tok = c.post("/api/auth/login", json={"username": "rdr", "password": "pw12345678"}).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert c.get("/api/status", headers=h).status_code == 200            # VIEW ok
        assert c.post("/api/settings", json={"ui.theme": "dark"}, headers=h).status_code == 403


def test_unauthenticated_is_401_when_users_exist(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        assert c.get("/api/status").status_code == 401
```

(These reference `/api/auth/login`, added in Task 8. Run the whole file after Task 8; here run only the two that don't need login.)

Run: `uv run pytest ems/tests/test_auth_api.py::test_forced_onboarding_blocks_until_admin ems/tests/test_auth_api.py::test_unauthenticated_is_401_when_users_exist -q`
Expected: FAIL (currently reads are open / no onboarding gate).

- [ ] **Step 2: Add error responders + principal resolver** (in `create_app`, near `_auth_error` ~L775)

```python
    def _forbidden_error():
        return JSONResponse({"detail": "forbidden"}, status_code=403)

    def _onboarding_required_error():
        return JSONResponse({"detail": "onboarding_required"}, status_code=409)

    async def _resolve_principal(request: Request):
        if auth_store is None:
            return None
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme != "Bearer" or not token:
            return None
        return await auth_store.resolve(token)
```
`JSONResponse` is already imported in `api.py`. Note `_auth_error()` / `_cross_origin_error()` return ASGI-callable responses; `JSONResponse(...)` instances are themselves ASGI-callable, so `await _forbidden_error()(scope, receive, send)` works.

- [ ] **Step 3: Rewrite the token gate** in `_AccessMiddleware.__call__` (replace the block at ~L1332-1341)

Keep the origin gate above it untouched. Replace the token-gate `if` with:
```python
                # (2) identity gate — replaces the shared-token check
                if path.startswith("/api/") and path not in EXEMPT_PATHS:
                    if auth_store is None:
                        # LEGACY fallback (no user system wired, e.g. old tests): keep the exact
                        # pre-existing shared-token behaviour so nothing regresses.
                        if _effective_web_token() is not None:
                            is_write = is_write_method and path in _WRITE_API_PATHS
                            if (is_write or _read_auth_required()) and not _authorized(Request(scope)):
                                await _auth_error()(scope, receive, send)
                                return
                    else:
                        # IDENTITY gate (users exist / onboarding)
                        if not app.state.users_exist:
                            await _onboarding_required_error()(scope, receive, send)
                            return
                        principal = await _resolve_principal(Request(scope))
                        if principal is None:
                            await _auth_error()(scope, receive, send)
                            return
                        method = scope.get("method", "GET").upper()
                        if not role_satisfies(principal.role, required_tier(path, method)):
                            await _forbidden_error()(scope, receive, send)
                            return
                        if requires_session(path) and principal.kind != "session":
                            await _forbidden_error()(scope, receive, send)
                            return
                        scope["auth_principal"] = principal
```
Add the imports at the top of `api.py`:
```python
from ems.web.authz import EXEMPT_PATHS, OPERATE_PATHS, required_tier, requires_session, role_satisfies
```
Replace the old `_AUTH_EXEMPT_API_PATHS` usage with `EXEMPT_PATHS`, and set `_WRITE_API_PATHS = OPERATE_PATHS` (~L1224) so any other reference (`app.state.write_api_paths`) stays valid and DRY. Leave `_authorized`/`_read_auth_required`/`_effective_web_token` defined (still used by onboarding anti-seizure in Task 9); they are simply no longer the gate.

- [ ] **Step 4: Update the mutating-route invariant test**

Find `test_every_mutating_route_is_write_gated_or_explicitly_exempt` (grep the tests). Replace its body so it asserts every mutating (`POST/PUT/PATCH/DELETE`) route either resolves to `required_tier != Tier.VIEW` **or** is in `EXEMPT_PATHS`:
```python
from ems.web.authz import EXEMPT_PATHS, Tier, required_tier

def test_every_mutating_route_declares_a_tier_or_is_exempt():
    app = _build_app_for_route_inspection()  # reuse the existing helper in this test module
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        mutating = methods & {"POST", "PUT", "PATCH", "DELETE"}
        if not mutating:
            continue
        path = route.path
        assert path in EXEMPT_PATHS or required_tier(path, "POST") != Tier.VIEW, (
            f"{path} is mutating but neither tiered above VIEW nor exempt"
        )
```
Match the existing test's app-construction helper; keep its name discoverable or update references.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_api.py -k "onboarding or unauthenticated" -q`
Expected: PASS. Also run the full suite to catch reads-now-gated regressions: `uv run pytest ems/tests -q` (expect failures only in tests that assumed open reads — fix those tests to log in / pass a token; note them in the commit).

- [ ] **Step 6: Commit**

```bash
git add ems/web/api.py ems/tests/
git commit -m "feat(auth): identity-based middleware gate + forced onboarding (401/403/409)"
```

---

### Task 8: Auth endpoints (`ems/web/routes/auth.py`)

**Files:**
- Create: `ems/web/routes/auth.py`
- Modify: `ems/web/api.py` (import + include in the router loop ~L3443)
- Test: `ems/tests/test_auth_api.py`

**Interfaces:**
- Consumes: `ctx.auth_store`; `hash_password`, `verify_password`, `dummy_verify` (Task 1); `scope["auth_principal"]` (Task 7).
- Produces: `build_router(ctx) -> APIRouter` serving `/api/auth` (discovery), `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`, `/api/auth/password`.

- [ ] **Step 1: Write the failing tests** (append to `test_auth_api.py`)

```python
def test_login_me_and_logout(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "pw12345678"})
        assert r.status_code == 200
        tok = r.json()["token"]
        assert r.json()["user"] == {"username": "admin", "role": "admin"}
        h = {"Authorization": f"Bearer {tok}"}
        assert c.get("/api/auth/me", headers=h).json()["role"] == "admin"
        assert c.post("/api/auth/logout", headers=h).status_code == 200
        assert c.get("/api/auth/me", headers=h).status_code == 401  # session revoked


def test_login_bad_password_401(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        assert c.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "ghost", "password": "x"}).status_code == 401


def test_change_password_requires_session_not_access_token(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")
    # mint an access token directly
    from ems.storage.auth import AuthStore as _AS
    acc = {}

    async def _mint():
        s = _AS(db)
        await s.init()
        u = await s.get_user_by_username("u")
        acc["raw"] = await s.create_token(u["id"], "access", name="script")
        await s.close()

    asyncio.run(_mint())
    with TestClient(_app(db)) as c:
        h = {"Authorization": f"Bearer {acc['raw']}"}
        # access token: VIEW works, session-only write is 403
        assert c.get("/api/auth/me", headers=h).status_code == 200
        assert c.post("/api/auth/password", json={"old": "pw12345678", "new": "newpass123"},
                      headers=h).status_code == 403
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest ems/tests/test_auth_api.py -k "login or logout or change_password" -q`
Expected: FAIL — 404 on `/api/auth/login` (route not registered yet).

- [ ] **Step 3: Implement `ems/web/routes/auth.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ems.authn import dummy_verify, hash_password, verify_password
from ems.web.context import AppContext


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    auth_store = ctx.auth_store

    @router.get("/api/auth")
    async def auth_discovery(request: Request) -> dict:
        # /api/auth is EXEMPT, so the middleware does NOT set scope["auth_principal"] here —
        # resolve it ourselves so `authenticated` is truthful.
        principal = None
        if auth_store is not None:
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme == "Bearer" and token:
                principal = await auth_store.resolve(token)
        return {
            "required": True,
            "authenticated": principal is not None,
            "onboarding_needed": not request.app.state.users_exist,
            "user": ({"username": principal.username, "role": principal.role}
                     if principal else None),
        }

    @router.post("/api/auth/login")
    async def login(request: Request, body: dict | None = None) -> JSONResponse:
        body = body or {}
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        user = await auth_store.get_user_by_username(username) if username else None
        if user is None or user["disabled"] or not verify_password(user["password_hash"], password):
            if user is None:
                dummy_verify()
            return JSONResponse({"detail": "invalid credentials"}, status_code=401)
        raw = await auth_store.create_token(user["id"], "session")
        return JSONResponse({"token": raw, "user": {"username": user["username"], "role": user["role"]}})

    @router.post("/api/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        principal = request.scope.get("auth_principal")
        if principal is not None:
            await auth_store.revoke_token(principal.token_id, principal.user_id)
        return JSONResponse({"ok": True})

    @router.get("/api/auth/me")
    async def me(request: Request) -> dict:
        p = request.scope.get("auth_principal")
        return {"username": p.username, "role": p.role, "kind": p.kind}

    @router.post("/api/auth/password")
    async def change_password(request: Request, body: dict | None = None) -> JSONResponse:
        body = body or {}
        p = request.scope.get("auth_principal")
        user = await auth_store.get_user_by_id(p.user_id)
        if not verify_password(user["password_hash"], str(body.get("old", ""))):
            return JSONResponse({"detail": "invalid credentials"}, status_code=403)
        new = str(body.get("new", ""))
        if len(new) < 8:
            return JSONResponse({"detail": "password too short (min 8)"}, status_code=422)
        await auth_store.set_password(p.user_id, hash_password(new))
        return JSONResponse({"ok": True})

    return router
```

- [ ] **Step 4: Register the router** (`ems/web/api.py`)

Add the import beside the other route imports (~L138):
```python
from ems.web.routes.auth import build_router as build_auth_router
```
Add `build_auth_router` to the include tuple (~L3443):
```python
    for build in (build_auth_router, build_car_router, build_digest_router, build_notify_router,
                  build_export_router, build_accuracy_router, build_whatif_router):
        app.include_router(build(ctx))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_api.py -q`
Expected: PASS (login/logout/me/password + the Task 7 role tests).

- [ ] **Step 6: Commit**

```bash
git add ems/web/routes/auth.py ems/web/api.py ems/tests/test_auth_api.py
git commit -m "feat(auth): /api/auth login/logout/me/password + discovery"
```

---

### Task 9: Onboarding endpoint + atomic first-admin transaction

**Files:**
- Modify: `ems/storage/auth.py` (`onboard_admin`), `ems/web/routes/auth.py` (`/api/auth/onboard`), `ems/web/context.py` + `ems/web/api.py` (expose `effective_web_token` on ctx)
- Test: `ems/tests/test_auth_onboarding.py`

**Interfaces:**
- Produces: `async AuthStore.onboard_admin(username, password_hash, *, migrate_token_hash: str | None) -> tuple[int, str] | None` (returns `(user_id, session_raw)` or `None` if a user already exists — one `BEGIN IMMEDIATE`); `AppContext.effective_web_token: Callable[[], str | None]`; `POST /api/auth/onboard`.

- [ ] **Step 1: Write the failing tests**

Create `ems/tests/test_auth_onboarding.py`:
```python
import asyncio

from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.storage.auth import AuthStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


def _app(db, *, token=None):
    return create_app(MockSource(), dry_run=True, dev_mode="mock",
                      settings_store=SettingsStore(db), auth_store=AuthStore(db),
                      web_auth_token=token)


def test_onboard_creates_admin_then_gates_reopen(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(_app(db)) as c:
        assert c.get("/api/status").status_code == 409
        r = c.post("/api/auth/onboard", json={"username": "admin", "password": "pw12345678"})
        assert r.status_code == 200
        tok = r.json()["token"]
        assert c.get("/api/status", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
        # onboarding now closed
        assert c.post("/api/auth/onboard", json={"username": "x", "password": "yyyyyyyy"}).status_code == 409


def test_onboard_requires_shared_token_and_migrates_it(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(_app(db, token="legacy-shared")) as c:
        assert c.post("/api/auth/onboard", json={"username": "a", "password": "pw12345678"}).status_code == 403
        r = c.post("/api/auth/onboard",
                   json={"username": "a", "password": "pw12345678", "shared_token": "legacy-shared"})
        assert r.status_code == 200
        # the old shared token now works as a migrated access token
        assert c.get("/api/status", headers={"Authorization": "Bearer legacy-shared"}).status_code == 200


def test_concurrent_onboard_yields_single_admin(tmp_path):
    from ems.authn import hash_password
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        results = await asyncio.gather(
            s.onboard_admin("a", hash_password("pw12345678"), migrate_token_hash=None),
            s.onboard_admin("b", hash_password("pw12345678"), migrate_token_hash=None),
            return_exceptions=True,
        )
        return results, await s.user_count()

    results, count = asyncio.run(run())
    assert count == 1
    assert sum(1 for r in results if isinstance(r, tuple)) == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest ems/tests/test_auth_onboarding.py -q`
Expected: FAIL — `AttributeError: 'AuthStore' object has no attribute 'onboard_admin'` / 404 on onboard.

- [ ] **Step 3: Add `onboard_admin`** (in `AuthStore`, `ems/storage/auth.py`)

```python
    async def onboard_admin(self, username: str, password_hash: str, *,
                            migrate_token_hash: str | None) -> tuple[int, str] | None:
        """Create the first admin + its session (+ migrated access token) atomically.

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
                    "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
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
```

- [ ] **Step 4: Expose `effective_web_token` on the context** (`ems/web/context.py` + `ems/web/api.py`)

In `context.py`, add to the helper-callables group (~L88):
```python
    effective_web_token: Callable[[], str | None]
```
In `api.py`'s `AppContext(...)` construction, pass it (the closure already exists at ~L754):
```python
        effective_web_token=_effective_web_token,
```

- [ ] **Step 5: Add the `/api/auth/onboard` endpoint** (`ems/web/routes/auth.py`)

Add the import at the top:
```python
import secrets

from ems.authn import hash_token
```
Add inside `build_router`:
```python
    @router.post("/api/auth/onboard")
    async def onboard(request: Request, body: dict | None = None) -> JSONResponse:
        if request.app.state.users_exist:
            return JSONResponse({"detail": "already onboarded"}, status_code=409)
        body = body or {}
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        if len(username) < 1 or len(password) < 8:
            return JSONResponse({"detail": "username required; password min 8"}, status_code=422)
        shared = ctx.effective_web_token()
        migrate_hash = None
        if shared is not None:  # anti-seizure: prove control of the existing shared token
            if not secrets.compare_digest(str(body.get("shared_token", "")), shared):
                return JSONResponse({"detail": "shared token required"}, status_code=403)
            migrate_hash = hash_token(shared)
        result = await auth_store.onboard_admin(username, hash_password(password),
                                                migrate_token_hash=migrate_hash)
        if result is None:
            return JSONResponse({"detail": "already onboarded"}, status_code=409)
        _uid, raw = result
        request.app.state.users_exist = True
        return JSONResponse({"token": raw, "user": {"username": username, "role": "admin"}})
```

Also extend the `/api/auth` discovery handler (Task 8) to tell the client whether the onboarding
form must show the shared-token field:
```python
            "shared_token_required": (not request.app.state.users_exist)
                                     and ctx.effective_web_token() is not None,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_onboarding.py -q`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add ems/storage/auth.py ems/web/routes/auth.py ems/web/context.py ems/web/api.py ems/tests/test_auth_onboarding.py
git commit -m "feat(auth): atomic first-admin onboarding + shared-token migration + anti-seizure"
```

---

### Task 10: Web onboarding + login gate (frontend)

**Files:**
- Modify: `ems/web/frontend/src/auth.ts` (add `clearToken`)
- Create: `ems/web/frontend/src/Onboarding.tsx`, `ems/web/frontend/src/Login.tsx`
- Modify: `ems/web/frontend/src/App.tsx` (auth gate + 401 handling), `ems/web/frontend/src/Settings.tsx` (retire the paste-token box; add a Logout button)
- Test: `ems/web/frontend/e2e/auth.spec.ts`

**Interfaces:**
- Consumes: `GET /api/auth`, `POST /api/auth/login`, `POST /api/auth/onboard`, `POST /api/auth/logout`; `getToken`/`setToken`/`clearToken`/`authHeaders` from `auth.ts`.

- [ ] **Step 1: Add `clearToken`** to `ems/web/frontend/src/auth.ts`
```ts
export function clearToken(): void {
  localStorage.removeItem('ems.token');
}
```

- [ ] **Step 2: Create `Onboarding.tsx`**
```tsx
import { useState } from 'react';
import { setToken } from './auth';

export function Onboarding({ sharedTokenRequired, onDone }: { sharedTokenRequired: boolean; onDone: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [shared, setShared] = useState('');
  const [error, setError] = useState('');
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    const body: Record<string, string> = { username, password };
    if (sharedTokenRequired) body.shared_token = shared;
    const r = await fetch('/api/auth/onboard', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!r.ok) { setError((await r.json().catch(() => ({}))).detail ?? 'Onboarding failed'); return; }
    setToken((await r.json()).token);
    onDone();
  }
  return (
    <form onSubmit={submit} data-testid="onboarding">
      <h1>Create your admin account</h1>
      <input aria-label="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
      <input aria-label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
      {sharedTokenRequired && (
        <input aria-label="Existing access token" value={shared} onChange={(e) => setShared(e.target.value)} />
      )}
      <button type="submit">Create admin</button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
```

- [ ] **Step 3: Create `Login.tsx`**
```tsx
import { useState } from 'react';
import { setToken } from './auth';

export function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    const r = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!r.ok) { setError('Invalid credentials'); return; }
    setToken((await r.json()).token);
    onDone();
  }
  return (
    <form onSubmit={submit} data-testid="login">
      <h1>Sign in</h1>
      <input aria-label="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
      <input aria-label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
      <button type="submit">Sign in</button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
```

- [ ] **Step 4: Gate the app** in `App.tsx`

At the top of the `App` component, add an auth-state check that runs on mount (follow the existing `useEffect`/`fetch` pattern in `App.tsx`, spreading `authHeaders()`):
```tsx
type AuthState = { onboarding_needed: boolean; authenticated: boolean; shared_token_required: boolean } | null;
const [auth, setAuth] = useState<AuthState>(null);
const refreshAuth = useCallback(async () => {
  const r = await fetch('/api/auth', { headers: { ...authHeaders() } });
  setAuth(await r.json());
}, []);
useEffect(() => { refreshAuth(); }, [refreshAuth]);

if (auth === null) return null; // or the existing splash
if (auth.onboarding_needed) return <Onboarding sharedTokenRequired={auth.shared_token_required} onDone={refreshAuth} />;
if (!auth.authenticated) return <Login onDone={refreshAuth} />;
```
`shared_token_required` comes from the discovery payload (extended in Task 9), so the onboarding form
shows the "existing access token" field only when a legacy shared token is configured.
Also add a **global 401 handler**: when any authed `fetch` returns 401, call `clearToken()` then
`refreshAuth()` so the app falls back to `<Login/>`. Wire this into the shared fetch path if one
exists; otherwise add it to the App-level poller.

- [ ] **Step 5: Retire the paste-token box + add Logout** in `Settings.tsx`

Remove the "Access & security" access-token `<input data-testid="access-token">` and its Save button (machine tokens are minted elsewhere now). Add a Logout button:
```tsx
<button onClick={async () => { await fetch('/api/auth/logout', { method: 'POST', headers: { ...authHeaders() } }); clearToken(); location.reload(); }}>Log out</button>
```

- [ ] **Step 6: Write the e2e test** (extend `ems/web/frontend/e2e/auth.spec.ts`)

```ts
import { test, expect } from '@playwright/test';

test('onboarding then login then logout', async ({ page }) => {
  // fresh DB (see e2e clean-DB harness). First load → onboarding.
  await page.goto('/');
  await expect(page.getByTestId('onboarding')).toBeVisible();
  await page.getByLabel('Username').fill('admin');
  await page.getByLabel('Password').fill('pw12345678');
  await page.getByRole('button', { name: 'Create admin' }).click();
  await expect(page.getByTestId('onboarding')).toBeHidden();

  // reload after clearing the token → login screen
  await page.evaluate(() => localStorage.removeItem('ems.token'));
  await page.reload();
  await expect(page.getByTestId('login')).toBeVisible();
  await page.getByLabel('Username').fill('admin');
  await page.getByLabel('Password').fill('pw12345678');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByTestId('login')).toBeHidden();
});
```
Ensure the e2e harness points at a **clean DB** (repoint `db_path`) — see the existing e2e setup; AI-state tests fail against the live DB.

- [ ] **Step 7: Build + run**

Run: `cd ems/web/frontend && npm run build` (must succeed; bundle ≤300 KB gz)
Run: the Playwright suite per the repo's e2e command (e.g. `npm run test:e2e`) against a clean DB.
Expected: the onboarding→login→logout spec passes.

- [ ] **Step 8: Commit**

```bash
git add ems/web/frontend/src/auth.ts ems/web/frontend/src/Onboarding.tsx ems/web/frontend/src/Login.tsx ems/web/frontend/src/App.tsx ems/web/frontend/src/Settings.tsx ems/web/frontend/e2e/auth.spec.ts
git commit -m "feat(auth): web onboarding + login gate, logout, retire paste-token box"
```

---

## Verification (end of slice)

- [ ] Full backend suite green: `uv run pytest ems/tests -q`
- [ ] Lint clean: `uv run ruff check ems`
- [ ] Frontend builds and the auth e2e passes against a clean DB.
- [ ] Manual smoke: boot with a fresh DB → forced onboarding; create admin → dashboard loads; `EMS_WEB_TOKEN=legacy` set → onboarding demands it and the migrated token still authorizes `/api/status`.

## Out of scope (later slices)

- Slice 2: invites + `routes/users.py` (user CRUD, role change, disable, last-admin guard), admin UI, reader read-only UI.
- Slice 3: long-lived token mint/list/revoke UI + `replace:true`; iOS login + per-device widget token.
- Slice 4: login rate-limiting/lockout, strict CSP, audit-log wiring, export redaction of auth tables, full e2e matrix.
