# Auth Slice 5 — Token Scoping, Idle Expiry, Admin Session-Gating — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give access tokens a per-token privilege tier (default read-only, capped at owner), idle auto-revoke, and make user/invite management session-only, so no machine token can escalate or persist forever.

**Architecture:** One nullable `auth_tokens.tier` column. All privilege is decided in one pure function, `authz.effective_rank()` = `min(owner_role_rank, token_scope_rank)`, computed live on every request and failing closed on malformed data. The pure-ASGI identity gate (`_AccessMiddleware`) switches from `role_satisfies(owner_role, …)` to `effective_rank(…)`, and `/api/users*` + `/api/invites*` join the session-only set. Idle expiry is enforced lazily in `AuthStore.resolve()`.

**Tech Stack:** Python 3.12 / FastAPI (pure-ASGI middleware), aiosqlite/SQLite, React+Vite (TSX), Swift (iOS EMSControlCore), pytest + Playwright + `swift test`.

## Global Constraints

- **Fail closed, always.** Never index `_TIER_RANK[tier]` directly — the gate runs OUTSIDE `resolve()`'s try/except (`api.py:1489-1496`), so a `KeyError` is an uncaught HTTP 500 (fail-open crash). Use `.get(tier, -1)`. An unknown/garbage tier → rank `-1` → below VIEW → denied.
- **Effective tier = `min(owner_role, token_scope)`**, recomputed every request (demoting the owner demotes their tokens). Legacy `tier IS NULL` access tokens cap at **OPERATE**; sessions always resolve at the owner's full live role.
- **Default mint tier = `view`** (read-only). Mint validates the requested tier is a known value AND its rank ≤ the caller's owner-role rank; both fail with **HTTP 400**.
- **Session-only prefixes:** `/api/auth/tokens`, `/api/users`, `/api/invites`. `/api/invites/accept` stays reachable unauthenticated (it is in `EXEMPT_PATHS`, checked before the session gate at `api.py:1471`).
- **Idle expiry:** config `auth.access_token_idle_days` (default **90**); a value `<= 0` **disables** the check (never "expire everything"). Applies to `kind='access'` only.
- **Migrated shared token** is minted at tier `operate`.
- **iOS:** widget token minted at tier `view`; **no** `keychain-access-groups` entitlement / Keychain move — the token stays in app-group `UserDefaults`.
- **Middleware stays pure-ASGI** (auth invariant #1) — do not convert `_AccessMiddleware` to `BaseHTTPMiddleware`/`@app.middleware`.
- **Tests use no hardware.** Store tests follow the `AuthStore(str(tmp_path / "ems.sqlite"))` + `asyncio.run(run())` pattern; API tests use `fastapi.testclient.TestClient` via the `_app(db)` helper in `test_auth_api.py`. Frontend bundle stays ≤ 300 KB gz.
- **Tier vocabulary** is `("view","operate","admin")`, defined once in `ems/authn.py` as `VALID_TOKEN_TIERS`; it aligns by rank with the role ladder (`reader`/`user`/`admin`) and `authz.Tier`.

---

## File Structure

- `ems/authn.py` — add `VALID_TOKEN_TIERS` (the single source of the tier vocabulary; a leaf module imported by both storage and web).
- `ems/storage/auth.py` — `tier` column + idempotent migration; `Principal.token_tier`; tier persistence + validation in `create_token`/`replace_token`/`onboard_admin`; `resolve()` returns tier + enforces idle expiry; construction takes `access_token_idle_days`; optional `purge_idle_access_tokens()`.
- `ems/web/authz.py` — `_TIER_RANK`, `_LEGACY_ACCESS_CAP`, `role_rank()`, `tier_rank()`, `effective_rank()`; extend `_SESSION_ONLY_PREFIXES`.
- `ems/web/api.py` — gate uses `effective_rank`; audit-category `is_admin` uses `effective_rank`.
- `ems/web/routes/auth.py` — mint route accepts + validates `tier`; list returns `tier`.
- `ems/config.py` / `config.yaml` — `access_token_idle_days` field + parsing + bound.
- `ems/main.py` — wire `access_token_idle_days` into `AuthStore`.
- `ems/web/frontend/src/AccountTokens.tsx` — tier selector + badge.
- `ios/.../APIClient.swift`, `.../DashboardStore.swift`, `.../WidgetSupport.swift` — provision widget token at tier `view`.
- Tests: `test_auth_store.py`, `test_authz.py`, `test_auth_api.py`, `test_config.py`, an e2e spec, `APIClientTests.swift`.

---

### Task 1: Add the `tier` column + idempotent migration

**Files:**
- Modify: `ems/storage/auth.py` (`_TOKENS_DDL`, `AuthStore.init`)
- Test: `ems/tests/test_auth_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `auth_tokens.tier TEXT` (nullable; CHECK `tier IS NULL OR tier IN ('view','operate','admin')`) present on both fresh and migrated DBs.

- [ ] **Step 1: Write the failing tests**

Add to `ems/tests/test_auth_store.py`:

```python
def _token_columns(db_path: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(auth_tokens)").fetchall()}
    finally:
        con.close()


def test_fresh_db_has_tier_column(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    s = AuthStore(db)
    asyncio.run(_init_and_close(s))
    assert "tier" in _token_columns(db)


def test_tier_column_migration_is_idempotent(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    # Simulate a pre-slice-5 DB: auth_tokens WITHOUT the tier column.
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE auth_tokens (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
        "token_hash TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, name TEXT, "
        "created_at TEXT NOT NULL, last_used_at TEXT, expires_at TEXT)"
    )
    con.commit()
    con.close()
    s = AuthStore(db)

    async def run():
        await s.init()  # adds tier
        await s.init()  # must not fail on the second pass
        await s.close()

    asyncio.run(run())
    assert "tier" in _token_columns(db)
```

Add this helper near the top of the file (below `_tables`):

```python
async def _init_and_close(store: AuthStore) -> None:
    await store.init()
    await store.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jeroenniesen/Development/EnergyManagementSystem/.claude/worktrees/auth-slice1 && uv run pytest ems/tests/test_auth_store.py::test_fresh_db_has_tier_column ems/tests/test_auth_store.py::test_tier_column_migration_is_idempotent -v`
Expected: FAIL — `assert "tier" in {...}` (column not present).

- [ ] **Step 3: Add the column to the DDL and an idempotent ALTER**

In `ems/storage/auth.py`, change `_TOKENS_DDL` to include the tier column:

```python
_TOKENS_DDL = """
CREATE TABLE IF NOT EXISTS auth_tokens (
  id           INTEGER PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES users(id),
  token_hash   TEXT NOT NULL UNIQUE,
  kind         TEXT NOT NULL CHECK(kind IN ('session','access')),
  name         TEXT,
  created_at   TEXT NOT NULL,
  last_used_at TEXT,
  expires_at   TEXT,
  tier         TEXT CHECK(tier IS NULL OR tier IN ('view','operate','admin'))
)
"""
```

In `AuthStore.init`, after `await db.execute(_INVITES_DDL)` and before `await db.commit()`, add the migration:

```python
            await db.execute(_INVITES_DDL)
            # Slice 5: add auth_tokens.tier to pre-slice-5 DBs. The store only does CREATE TABLE
            # IF NOT EXISTS, so an existing table needs an explicit ALTER. Idempotent: skip when
            # the column is already there (fresh DBs get it from _TOKENS_DDL above). SQLite allows
            # a CHECK on ADD COLUMN; existing NULL-tier rows satisfy it.
            cur = await db.execute("PRAGMA table_info(auth_tokens)")
            cols = {row[1] for row in await cur.fetchall()}
            if "tier" not in cols:
                await db.execute(
                    "ALTER TABLE auth_tokens ADD COLUMN tier TEXT "
                    "CHECK(tier IS NULL OR tier IN ('view','operate','admin'))"
                )
            await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_store.py -v`
Expected: PASS (both new tests + all existing store tests).

- [ ] **Step 5: Commit**

```bash
git add ems/storage/auth.py ems/tests/test_auth_store.py
git commit -m "feat(auth): add nullable auth_tokens.tier column + idempotent migration"
```

---

### Task 2: Persist tier on token creation + carry it on `Principal`

**Files:**
- Modify: `ems/authn.py` (add `VALID_TOKEN_TIERS`)
- Modify: `ems/storage/auth.py` (`Principal`, `create_token`, `replace_token`, `onboard_admin`, `resolve`)
- Test: `ems/tests/test_auth_store.py`

**Interfaces:**
- Consumes: `auth_tokens.tier` (Task 1).
- Produces:
  - `ems.authn.VALID_TOKEN_TIERS = ("view", "operate", "admin")`
  - `Principal(user_id, username, role, token_id, kind, token_tier=None)` — new trailing field `token_tier: str | None`.
  - `AuthStore.create_token(user_id, kind, *, name=None, tier=None) -> str` (raises `ValueError` on an invalid non-None tier; stores tier only for `kind='access'`).
  - `AuthStore.replace_token(user_id, name, *, tier="view") -> str` (raises `ValueError` on an invalid tier).
  - `AuthStore.resolve()` returns a `Principal` whose `token_tier` is the stored column value.
  - Migrated shared token stored with `tier='operate'`.

- [ ] **Step 1: Write the failing tests**

Add to `ems/tests/test_auth_store.py`:

```python
def test_create_token_persists_tier_and_resolve_returns_it(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "admin")
        # session: tier ignored / NULL
        sess = await s.create_token(uid, "session")
        assert (await s.resolve(sess)).token_tier is None
        # access: default None (caller may omit), explicit view/operate persisted
        acc = await s.create_token(uid, "access", name="w", tier="view")
        assert (await s.resolve(acc)).token_tier == "view"
        op = await s.create_token(uid, "access", name="o", tier="operate")
        assert (await s.resolve(op)).token_tier == "operate"

    asyncio.run(run())


def test_create_token_rejects_invalid_tier(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "admin")
        raised = False
        try:
            await s.create_token(uid, "access", name="bad", tier="root")
        except ValueError:
            raised = True
        assert raised

    asyncio.run(run())


def test_replace_token_defaults_to_view_tier(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "admin")
        raw = await s.replace_token(uid, "iOS widget")
        assert (await s.resolve(raw)).token_tier == "view"

    asyncio.run(run())


def test_onboard_migrated_token_is_operate_tier(tmp_path):
    from ems.authn import hash_token
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        migrated_raw = "shared-secret-xyz"
        await s.onboard_admin("admin", "h", migrate_token_hash=hash_token(migrated_raw))
        p = await s.resolve(migrated_raw)
        assert p is not None and p.kind == "access" and p.token_tier == "operate"

    asyncio.run(run())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ems/tests/test_auth_store.py -k "tier or migrated" -v`
Expected: FAIL — `create_token`/`replace_token` take no `tier` kwarg (`TypeError`), `Principal` has no `token_tier`.

- [ ] **Step 3: Implement tier vocabulary, `Principal` field, persistence, and validation**

In `ems/authn.py`, add the vocabulary constant near the top (after the existing imports/constants):

```python
# Access-token privilege tiers (auth slice 5). Single source of truth for the vocabulary; aligns
# by rank with the role ladder (reader/user/admin) and ems.web.authz.Tier. Defined here (a leaf
# module) so both ems.storage.auth and ems.web.authz can import it without a layering cycle.
VALID_TOKEN_TIERS = ("view", "operate", "admin")
```

In `ems/storage/auth.py`:

Update the import:

```python
from ems.authn import VALID_TOKEN_TIERS, hash_token, new_token
```

Add `token_tier` to `Principal` (default keeps existing construction sites valid):

```python
@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str
    role: str
    token_id: int
    kind: str  # 'session' | 'access'
    token_tier: str | None = None  # slice 5: access-token scope ('view'|'operate'|'admin'); None
    #                                for sessions and legacy pre-slice access tokens
```

Rewrite `create_token`:

```python
    async def create_token(self, user_id: int, kind: str, *, name: str | None = None,
                           tier: str | None = None) -> str:
        if tier is not None and tier not in VALID_TOKEN_TIERS:
            raise ValueError(f"invalid token tier: {tier!r}")
        raw = new_token()
        now = datetime.now(UTC)
        expires = (now + _SESSION_TTL).isoformat() if kind == "session" else None
        stored_tier = tier if kind == "access" else None  # tier is meaningless for sessions
        async with self._write_conn() as db:
            await db.execute(
                "INSERT INTO auth_tokens (user_id, token_hash, kind, name, created_at, "
                "expires_at, tier) VALUES (?,?,?,?,?,?,?)",
                (user_id, hash_token(raw), kind, name, now.isoformat(), expires, stored_tier),
            )
            await db.commit()
        return raw
```

In `resolve`, add `t.tier` to the SELECT and pass it to `Principal`:

```python
            cur = await db.execute(
                "SELECT t.id AS token_id, t.kind, t.expires_at, t.last_used_at, t.tier, "
                "u.id AS user_id, u.username, u.role, u.disabled "
                "FROM auth_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?",
                (th,),
            )
```

```python
            return Principal(
                user_id=row["user_id"], username=row["username"], role=row["role"],
                token_id=row["token_id"], kind=row["kind"], token_tier=row["tier"],
            )
```

In `replace_token`, add the `tier` parameter + validation and persist it:

```python
    async def replace_token(self, user_id: int, name: str, *, tier: str = "view") -> str:
```

(keep the existing docstring), then at the top of the body:

```python
        if tier not in VALID_TOKEN_TIERS:
            raise ValueError(f"invalid token tier: {tier!r}")
        raw = new_token()
        now = datetime.now(UTC)
```

and change the INSERT inside the transaction to carry the tier:

```python
                await db.execute(
                    "INSERT INTO auth_tokens (user_id, token_hash, kind, name, created_at, "
                    "expires_at, tier) VALUES (?,?, 'access', ?, ?, NULL, ?)",
                    (user_id, hash_token(raw), name, now.isoformat(), tier),
                )
```

In `onboard_admin`, change the migrated-token INSERT to set `tier='operate'`:

```python
                if migrate_token_hash:
                    await db.execute(
                        "INSERT OR IGNORE INTO auth_tokens "
                        "(user_id, token_hash, kind, name, created_at, expires_at, tier) "
                        "VALUES (?,?, 'access', 'Migrated shared token', ?, NULL, 'operate')",
                        (uid, migrate_token_hash, now.isoformat()),
                    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_store.py ems/tests/test_auth_onboarding.py -v`
Expected: PASS (new tier tests + all existing store/onboarding tests, incl. `replace_token` concurrency).

- [ ] **Step 5: Commit**

```bash
git add ems/authn.py ems/storage/auth.py ems/tests/test_auth_store.py
git commit -m "feat(auth): persist per-token tier; Principal.token_tier; migrated token=operate"
```

---

### Task 3: `effective_rank()` + session-gate prefixes in `authz`

**Files:**
- Modify: `ems/web/authz.py`
- Test: `ems/tests/test_authz.py`

**Interfaces:**
- Consumes: `ems.authn.VALID_TOKEN_TIERS`.
- Produces (all in `ems.web.authz`):
  - `role_rank(role: str) -> int` (`-1` for unknown).
  - `tier_rank(tier: str | None) -> int` (`-1` for `None` or unknown — used to validate an *explicit* mint request).
  - `effective_rank(role: str, kind: str, token_tier: str | None) -> int` — session → owner rank; access → `min(owner, cap)` where cap is OPERATE for `None`, `.get(tier, -1)` otherwise.
  - `_SESSION_ONLY_PREFIXES == ("/api/auth/tokens", "/api/users", "/api/invites")`.

- [ ] **Step 1: Write the failing tests**

Add to `ems/tests/test_authz.py` (and extend the import line):

```python
from ems.web.authz import (
    Tier, required_tier, requires_session, role_satisfies,
    effective_rank, role_rank, tier_rank,
)


def test_tier_rank_and_role_rank():
    assert tier_rank("view") == 0 and tier_rank("operate") == 1 and tier_rank("admin") == 2
    assert tier_rank(None) == -1 and tier_rank("bogus") == -1
    assert role_rank("reader") == 0 and role_rank("user") == 1 and role_rank("admin") == 2
    assert role_rank("bogus") == -1


def test_effective_rank_session_is_owner_role():
    assert effective_rank("admin", "session", None) == int(Tier.ADMIN)
    assert effective_rank("user", "session", None) == int(Tier.OPERATE)


def test_effective_rank_access_is_min_of_owner_and_scope():
    # admin owner, read-only scope -> VIEW
    assert effective_rank("admin", "access", "view") == int(Tier.VIEW)
    # admin owner, operate scope -> OPERATE
    assert effective_rank("admin", "access", "operate") == int(Tier.OPERATE)
    # user owner, admin scope -> capped at owner (OPERATE)
    assert effective_rank("user", "access", "admin") == int(Tier.OPERATE)


def test_effective_rank_legacy_null_access_caps_at_operate():
    assert effective_rank("admin", "access", None) == int(Tier.OPERATE)
    assert effective_rank("user", "access", None) == int(Tier.OPERATE)


def test_effective_rank_fails_closed_on_garbage_tier():
    # Unknown non-null tier -> rank -1 -> below VIEW -> denies everything. Never KeyError.
    assert effective_rank("admin", "access", "root") == -1


def test_user_and_invite_management_is_session_only():
    assert requires_session("/api/users")
    assert requires_session("/api/users/5")
    assert requires_session("/api/invites")
    assert requires_session("/api/invites/9")
    # accept stays reachable unauthenticated — it's exempt, checked before requires_session.
    from ems.web.authz import EXEMPT_PATHS
    assert "/api/invites/accept" in EXEMPT_PATHS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ems/tests/test_authz.py -v`
Expected: FAIL — `ImportError: cannot import name 'effective_rank'`.

- [ ] **Step 3: Implement the tier ranking + session prefixes**

In `ems/web/authz.py`, add the import and rank tables near the top (after `_ROLE_RANK`):

```python
from ems.authn import VALID_TOKEN_TIERS

# Access-token scope ranks, aligned by position with the role ladder and Tier enum
# (view=0/VIEW, operate=1/OPERATE, admin=2/ADMIN). Single vocabulary source: ems.authn.
_TIER_RANK = {t: i for i, t in enumerate(VALID_TOKEN_TIERS)}
# Legacy access tokens (tier IS NULL, minted before slice 5) cap here — see effective_rank.
_LEGACY_ACCESS_CAP = _TIER_RANK["operate"]
```

Add the three functions (below `role_satisfies`):

```python
def role_rank(role: str) -> int:
    return _ROLE_RANK.get(role, -1)


def tier_rank(tier: str | None) -> int:
    """Rank of an EXPLICIT tier request (mint validation). None/unknown -> -1 (invalid)."""
    return -1 if tier is None else _TIER_RANK.get(tier, -1)


def effective_rank(role: str, kind: str, token_tier: str | None) -> int:
    """The privilege rank actually granted this request. Sessions get the owner's full live role;
    access tokens get min(owner, scope). FAILS CLOSED: NULL is the legacy OPERATE cap, any unknown
    non-null tier yields rank -1 (below VIEW) so it denies everything. NEVER index _TIER_RANK
    directly here — this runs at the gate (api.py), OUTSIDE resolve()'s try/except, so a KeyError
    would surface as an uncaught HTTP 500 instead of a 403."""
    owner = _ROLE_RANK.get(role, -1)
    if kind == "session":
        return owner
    cap = _LEGACY_ACCESS_CAP if token_tier is None else _TIER_RANK.get(token_tier, -1)
    return min(owner, cap)
```

Extend the session-only prefixes:

```python
_SESSION_ONLY_PREFIXES = ("/api/auth/tokens", "/api/users", "/api/invites")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_authz.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ems/web/authz.py ems/tests/test_authz.py
git commit -m "feat(auth): effective_rank (fail-closed) + session-gate /api/users*,/api/invites*"
```

---

### Task 4: Wire the gate to `effective_rank` (privilege + session enforcement)

**Files:**
- Modify: `ems/web/api.py` (authz import block; gate at `~1501`; audit `is_admin` at `~3546`)
- Test: `ems/tests/test_auth_api.py`

**Interfaces:**
- Consumes: `authz.effective_rank`, `Principal.token_tier`.
- Produces: the middleware denies with 403 when `effective_rank(...) < required_tier`, and session-gates `/api/users*`+`/api/invites*`; the `/api/audit` handler treats admin-ness by effective tier.

- [ ] **Step 1: Write the failing tests**

Add to `ems/tests/test_auth_api.py` (reuse the existing `_app`, `_seed_user`, and `TestClient` patterns). Add a small helper to mint a scoped token directly via the store:

```python
def _seed_user_and_token(db: str, username: str, password: str, role: str,
                         *, kind: str, tier: str | None = None) -> str:
    from ems.authn import hash_password
    s = AuthStore(db)

    async def run():
        await s.init()
        uid = await s.create_user(username, hash_password(password), role)
        raw = (await s.create_token(uid, "session")) if kind == "session" \
            else (await s.create_token(uid, "access", name="t", tier=tier))
        await s.close()
        return raw

    return asyncio.run(run())


def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def test_view_scoped_access_token_is_forbidden_on_operate_write(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    tok = _seed_user_and_token(db, "admin", "pw12345678", "admin", kind="access", tier="view")
    with TestClient(_app(db)) as c:
        # a VIEW read is allowed
        assert c.get("/api/status", headers=_hdr(tok)).status_code == 200
        # an OPERATE write is denied for a read-only token even though the OWNER is admin
        r = c.post("/api/settings", headers=_hdr(tok), json={})
        assert r.status_code == 403


def test_operate_scoped_access_token_forbidden_on_admin_surface(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    tok = _seed_user_and_token(db, "admin", "pw12345678", "admin", kind="access", tier="operate")
    with TestClient(_app(db)) as c:
        assert c.get("/api/users", headers=_hdr(tok)).status_code == 403  # admin surface


def test_user_and_invite_management_requires_session(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    # admin-owned ACCESS token (even admin-scoped) must be rejected on account management
    atok = _seed_user_and_token(db, "admin", "pw12345678", "admin", kind="access", tier="admin")
    with TestClient(_app(db)) as c:
        assert c.get("/api/users", headers=_hdr(atok)).status_code == 403
        assert c.post("/api/invites", headers=_hdr(atok), json={"role": "user"}).status_code == 403
    # an admin SESSION succeeds on the same routes
    stok = _seed_user_and_token(db, "boss", "pw12345678", "admin", kind="session")
    with TestClient(_app(db)) as c:
        assert c.get("/api/users", headers=_hdr(stok)).status_code == 200
        assert c.post("/api/invites", headers=_hdr(stok),
                      json={"role": "user"}).status_code == 200


def test_malformed_tier_row_fails_closed_not_500(tmp_path):
    import sqlite3
    from datetime import UTC, datetime
    from ems.authn import hash_token, new_token
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    raw = new_token()
    # Bind created_at/last_used_at as tz-aware ISO, exactly as real tokens store them — a naive
    # SQLite datetime('now') would make the new idle-check subtraction (aware now - naive) raise.
    now_iso = datetime.now(UTC).isoformat()
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO auth_tokens (user_id, token_hash, kind, name, created_at, "
        "last_used_at, expires_at, tier) VALUES "
        "((SELECT id FROM users WHERE username='admin'), ?, 'access', 'x', ?, ?, NULL, 'garbage')",
        (hash_token(raw), now_iso, now_iso),
    )
    con.commit()
    con.close()
    with TestClient(_app(db)) as c:
        # a garbage tier denies even a VIEW read — effective_rank returns -1 (fail closed) -> 403,
        # never a 500/KeyError.
        assert c.get("/api/status", headers=_hdr(raw)).status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ems/tests/test_auth_api.py -k "scoped or requires_session or malformed" -v`
Expected: FAIL — access tokens currently inherit the owner's full role (403s are 200s; `/api/users` on an access token is 200), and the garbage-tier row would 200 (or 500).

- [ ] **Step 3: Wire the gate**

In `ems/web/api.py`, add `effective_rank` to the authz import block:

```python
from ems.web.authz import (
    EXEMPT_PATHS,
    OPERATE_PATHS,
    Tier,
    effective_rank,
    required_tier,
    requires_session,
    role_satisfies,
)
```

Replace the tier check in `_AccessMiddleware` (the line currently reading
`if not role_satisfies(principal.role, required_tier(path, method)):`):

```python
                        method = scope.get("method", "GET").upper()
                        if effective_rank(
                            principal.role, principal.kind, principal.token_tier
                        ) < int(required_tier(path, method)):
                            await _forbidden_error()(scope, receive, send)
                            return
                        if requires_session(path) and principal.kind != "session":
                            await _forbidden_error()(scope, receive, send)
                            return
```

Change the audit-category admin check (currently
`is_admin = principal is None or role_satisfies(principal.role, Tier.ADMIN)`):

```python
        principal = request.scope.get("auth_principal")
        is_admin = principal is None or effective_rank(
            principal.role, principal.kind, principal.token_tier) >= int(Tier.ADMIN)
```

(`role_satisfies` stays imported — it is still used elsewhere in `api.py`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_api.py ems/tests/test_auth_hardening.py -v`
Expected: PASS (new gate tests + all existing auth API/hardening tests — the existing session-based admin tests still pass because a session's effective rank equals the owner's role).

- [ ] **Step 5: Commit**

```bash
git add ems/web/api.py ems/tests/test_auth_api.py
git commit -m "feat(auth): gate on effective_rank; session-gate account mgmt; audit admin by tier"
```

---

### Task 5: Idle auto-revoke + config wiring

**Files:**
- Modify: `ems/storage/auth.py` (`__init__`, `resolve`, new `purge_idle_access_tokens`)
- Modify: `ems/config.py` (`Config` + `load_config`), `config.yaml`
- Modify: `ems/main.py` (construction)
- Test: `ems/tests/test_auth_store.py`, `ems/tests/test_config.py`

**Interfaces:**
- Consumes: `Config.access_token_idle_days`.
- Produces:
  - `AuthStore(db_path, *, access_token_idle_days: int = 90)`; `<= 0` disables idle expiry.
  - `resolve()` returns `None` for an access token idle beyond the TTL.
  - `AuthStore.purge_idle_access_tokens() -> int` (best-effort hygiene; no-op when disabled).
  - `Config.access_token_idle_days: int` (default 90, clamped `>= 0`).

- [ ] **Step 1: Write the failing tests**

Add to `ems/tests/test_auth_store.py`:

```python
def _backdate_token(db_path: str, token_id: int, iso: str) -> None:
    con = sqlite3.connect(db_path)
    con.execute("UPDATE auth_tokens SET last_used_at=?, created_at=? WHERE id=?",
                (iso, iso, token_id))
    con.commit()
    con.close()


def test_idle_access_token_stops_resolving(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    s = AuthStore(db, access_token_idle_days=30)

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "admin")
        raw = await s.create_token(uid, "access", name="w", tier="view")
        p = await s.resolve(raw)  # fresh -> resolves
        assert p is not None
        return p.token_id

    tid = asyncio.run(run())
    # backdate activity 60 days -> idle beyond the 30-day TTL
    from datetime import UTC, datetime, timedelta
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    _backdate_token(db, tid, old)
    assert asyncio.run(_resolve_once(db, raw, idle_days=30)) is None


async def _resolve_once(db: str, raw: str, *, idle_days: int):
    store = AuthStore(db, access_token_idle_days=idle_days)
    p = await store.resolve(raw)
    await store.close()
    return p


def test_idle_expiry_disabled_when_zero(tmp_path):
    db = str(tmp_path / "ems.sqlite")

    async def seed():
        s = AuthStore(db, access_token_idle_days=0)
        await s.init()
        uid = await s.create_user("a", "h", "admin")
        raw = await s.create_token(uid, "access", name="w", tier="view")
        p = await s.resolve(raw)
        await s.close()
        return raw, p.token_id

    raw, tid = asyncio.run(seed())
    from datetime import UTC, datetime, timedelta
    _backdate_token(db, tid, (datetime.now(UTC) - timedelta(days=9999)).isoformat())
    # idle disabled (0) -> an ancient token still resolves
    assert asyncio.run(_resolve_once(db, raw, idle_days=0)) is not None


def test_session_unaffected_by_access_idle_rule(tmp_path):
    db = str(tmp_path / "ems.sqlite")

    async def run():
        s = AuthStore(db, access_token_idle_days=1)
        await s.init()
        uid = await s.create_user("a", "h", "admin")
        raw = await s.create_token(uid, "session")
        p = await s.resolve(raw)
        await s.close()
        return raw, p.token_id

    raw, tid = asyncio.run(run())
    from datetime import UTC, datetime, timedelta
    _backdate_token(db, tid, (datetime.now(UTC) - timedelta(days=5)).isoformat())
    # a session past the access idle window still resolves (its own 30-day expiry governs it)
    assert asyncio.run(_resolve_once(db, raw, idle_days=1)) is not None


def test_purge_idle_access_tokens_removes_only_idle_access(tmp_path):
    db = str(tmp_path / "ems.sqlite")

    async def run():
        s = AuthStore(db, access_token_idle_days=30)
        await s.init()
        uid = await s.create_user("a", "h", "admin")
        live = await s.create_token(uid, "access", name="live", tier="view")
        dead = await s.create_token(uid, "access", name="dead", tier="view")
        live_id = (await s.resolve(live)).token_id
        dead_id = (await s.resolve(dead)).token_id
        await s.close()
        return live, live_id, dead_id

    live, live_id, dead_id = asyncio.run(run())
    from datetime import UTC, datetime, timedelta
    _backdate_token(db, dead_id, (datetime.now(UTC) - timedelta(days=60)).isoformat())

    async def purge():
        s = AuthStore(db, access_token_idle_days=30)
        n = await s.purge_idle_access_tokens()
        alive = await s.resolve(live)
        await s.close()
        return n, alive

    n, alive = asyncio.run(purge())
    assert n == 1 and alive is not None
```

Add to `ems/tests/test_config.py`:

```python
def test_access_token_idle_days_default_is_90(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("site:\n  timezone: Europe/Amsterdam\n")
    assert load_config(p).access_token_idle_days == 90


def test_access_token_idle_days_parsed(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("auth:\n  access_token_idle_days: 45\n")
    assert load_config(p).access_token_idle_days == 45


def test_access_token_idle_days_negative_clamps_to_zero(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("auth:\n  access_token_idle_days: -5\n")
    assert load_config(p).access_token_idle_days == 0
```

(If `test_config.py` doesn't already import `load_config`, add `from ems.config import load_config`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ems/tests/test_auth_store.py -k idle ems/tests/test_auth_store.py::test_purge_idle_access_tokens_removes_only_idle_access ems/tests/test_config.py -k idle -v`
Expected: FAIL — `AuthStore.__init__` takes no `access_token_idle_days`; `purge_idle_access_tokens` missing; `Config` has no `access_token_idle_days`.

- [ ] **Step 3: Implement idle expiry + config**

In `ems/storage/auth.py`, extend `__init__`:

```python
    def __init__(self, db_path: str, *, access_token_idle_days: int = 90) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._last_reheal_at: datetime | None = None
        # Slice 5: access tokens unused beyond this stop resolving. <= 0 DISABLES the check
        # (never "expire everything"); a positive value is the idle window.
        self._access_idle_ttl = (
            timedelta(days=access_token_idle_days) if access_token_idle_days > 0 else None
        )
```

In `resolve`, add `t.created_at` to the SELECT (alongside `t.tier` from Task 2):

```python
                "SELECT t.id AS token_id, t.kind, t.expires_at, t.last_used_at, t.tier, "
                "t.created_at, u.id AS user_id, u.username, u.role, u.disabled "
```

Then, immediately after the `if row is None or row["disabled"]: return None` guard, add the idle check (before the existing expiry/`dirty` block):

```python
            # Slice 5: idle auto-revoke for access tokens (sessions have their own expiry below).
            # last_used_at is throttled telemetry, but day-grained idle is unaffected; fall back
            # to created_at for a token that has never been used since mint.
            if row["kind"] == "access" and self._access_idle_ttl is not None:
                ref = row["last_used_at"] or row["created_at"]
                if (now - datetime.fromisoformat(ref)) > self._access_idle_ttl:
                    return None
```

Add `purge_idle_access_tokens` (place it after `revoke_token`):

```python
    async def purge_idle_access_tokens(self) -> int:
        """Best-effort hygiene: delete access tokens idle past the TTL. Lazy rejection in
        resolve() is the authoritative security mechanism; this just reclaims dead rows. No-op
        when idle expiry is disabled. Wire into existing periodic maintenance if present — do NOT
        add a new background loop for it."""
        if self._access_idle_ttl is None:
            return 0
        cutoff = (datetime.now(UTC) - self._access_idle_ttl).isoformat()
        async with self._write_conn() as db:
            cur = await db.execute(
                "DELETE FROM auth_tokens WHERE kind='access' "
                "AND COALESCE(last_used_at, created_at) < ?",
                (cutoff,),
            )
            await db.commit()
            return cur.rowcount
```

In `ems/config.py`, add the field to `Config` (with the other defaulted fields):

```python
    # Slice 5: an access token unused for this many days stops resolving (idle auto-revoke).
    # 0 disables the check (tokens never idle-expire); clamped >= 0 on load.
    access_token_idle_days: int = 90
```

In `load_config`, parse an `auth` section and pass the clamped value. Add near the other section reads:

```python
    auth = data.get("auth", {}) or {}
```

and in the returned `Config(...)`:

```python
        access_token_idle_days=max(0, int(auth.get("access_token_idle_days", 90))),
```

In `config.yaml`, document the default (add a top-level block):

```yaml
auth:
  # Access (machine) tokens unused for this many days stop working (idle auto-revoke).
  # 0 disables idle expiry. Sessions have their own 30-day sliding expiry regardless.
  access_token_idle_days: 90
```

In `ems/main.py`, wire the value (line 45):

```python
    auth_store = AuthStore(str(db_path), access_token_idle_days=cfg.access_token_idle_days)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_store.py ems/tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ems/storage/auth.py ems/config.py config.yaml ems/main.py ems/tests/test_auth_store.py ems/tests/test_config.py
git commit -m "feat(auth): idle auto-revoke for access tokens + auth.access_token_idle_days config"
```

---

### Task 6: Mint route — accept + validate `tier`, expose it in the list

**Files:**
- Modify: `ems/web/routes/auth.py` (`create_token_endpoint`)
- Modify: `ems/storage/auth.py` (`list_tokens` SELECT — add `tier`)
- Test: `ems/tests/test_auth_api.py`

**Interfaces:**
- Consumes: `authz.tier_rank`, `authz.role_rank`, `AuthStore.create_token(..., tier=)`, `AuthStore.replace_token(..., tier=)`.
- Produces: `POST /api/auth/tokens` accepts `tier` (default `"view"`); 400 on unknown tier or a tier above the caller's role. `GET /api/auth/tokens` rows include `tier`.

- [ ] **Step 1: Write the failing tests**

Add to `ems/tests/test_auth_api.py`:

```python
def _login(c, username, password):
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_mint_defaults_to_view_and_lists_tier(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        sess = _login(c, "u", "pw12345678")
        r = c.post("/api/auth/tokens", headers=_hdr(sess), json={"name": "script"})
        assert r.status_code == 200, r.text
        lst = c.get("/api/auth/tokens", headers=_hdr(sess)).json()["tokens"]
        minted = [t for t in lst if t["name"] == "script"][0]
        assert minted["tier"] == "view"


def test_mint_rejects_unknown_tier(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        sess = _login(c, "u", "pw12345678")
        r = c.post("/api/auth/tokens", headers=_hdr(sess), json={"name": "x", "tier": "root"})
        assert r.status_code == 400


def test_mint_rejects_tier_above_own_role(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")  # 'user' == OPERATE
    with TestClient(_app(db)) as c:
        sess = _login(c, "u", "pw12345678")
        r = c.post("/api/auth/tokens", headers=_hdr(sess), json={"name": "x", "tier": "admin"})
        assert r.status_code == 400


def test_mint_operate_tier_allowed_for_user(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        sess = _login(c, "u", "pw12345678")
        r = c.post("/api/auth/tokens", headers=_hdr(sess),
                   json={"name": "x", "tier": "operate"})
        assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ems/tests/test_auth_api.py -k mint -v`
Expected: FAIL — tier isn't accepted/validated and the list has no `tier` key.

- [ ] **Step 3: Implement the route + list change**

In `ems/storage/auth.py`, add `tier` to the `list_tokens` SELECT:

```python
            cur = await db.execute(
                "SELECT id, kind, name, created_at, last_used_at, expires_at, tier "
                "FROM auth_tokens WHERE user_id=? ORDER BY created_at",
                (user_id,),
            )
```

In `ems/web/routes/auth.py`, add the import:

```python
from ems.web.authz import role_rank, tier_rank
```

Rewrite the body of `create_token_endpoint` (keep the docstring):

```python
        body = body or {}
        principal = request.scope["auth_principal"]
        name = str(body.get("name", "")).strip()
        if not name:
            return JSONResponse({"detail": "name required"}, status_code=422)
        tier = str(body.get("tier", "view"))
        # Fail closed: unknown tier -> 400 (never reaches the store's ValueError).
        req = tier_rank(tier)
        if req < 0:
            return JSONResponse({"detail": "invalid tier"}, status_code=400)
        # No privilege escalation: a token can't out-rank the account minting it.
        if req > role_rank(principal.role):
            return JSONResponse({"detail": "tier exceeds your role"}, status_code=400)
        if body.get("replace"):
            raw = await auth_store.replace_token(principal.user_id, name, tier=tier)
            await ctx.audit_auth("token_replaced", f"Access token replaced: {name} ({tier})",
                                 username=principal.username, user_id=principal.user_id,
                                 token_name=name)
        else:
            raw = await auth_store.create_token(principal.user_id, "access", name=name, tier=tier)
            await ctx.audit_auth("token_minted", f"Access token minted: {name} ({tier})",
                                 username=principal.username, user_id=principal.user_id,
                                 token_name=name)
        return JSONResponse({"token": raw, "name": name, "tier": tier})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_auth_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ems/web/routes/auth.py ems/storage/auth.py ems/tests/test_auth_api.py
git commit -m "feat(auth): mint route validates tier (<=owner, default view); list exposes tier"
```

---

### Task 7: Frontend — tier selector on mint + tier badge in the list

**Files:**
- Modify: `ems/web/frontend/src/AccountTokens.tsx`
- Test: `ems/web/frontend/e2e/auth.spec.ts` (ADD a session-based test — see note below)

> **Why auth.spec.ts, not the `app` project:** the tokens manage UI renders only for a `kind==='session'` principal (`AccountTokens` shows the sign-in hint otherwise). The auth-aware `app` Playwright project rides an ACCESS token, so it would see the hint, not the mint form. The tokens UI is exercised against a real interactive session in `auth.spec.ts` (tokenless server, real login) — that file already has an "account tokens: mint shows the raw once…" test to model on. Add the new test there.

**Interfaces:**
- Consumes: `GET /api/auth/me` (`role`), `POST /api/auth/tokens {name, tier}`, `GET /api/auth/tokens` (rows carry `tier`).
- Produces: a labelled tier `<select>` (`data-testid="account-token-tier"`, default `view`, options limited to tiers ≤ the user's role) and a per-row badge (`data-testid="account-token-tier-badge"`).

- [ ] **Step 1: Write the failing e2e test**

Add a new test to `ems/web/frontend/e2e/auth.spec.ts`, AFTER the existing "account tokens: mint shows the raw once…" test. Session-based, mirroring that test's login + `nav-manage` flow (each test gets a fresh browser context; the admin already exists from the earlier onboarding test, so just log in):

```ts
test("account tokens: tier selector defaults to read-only and minted tokens show a tier badge",
  async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("login")).toBeVisible();
    await page.getByLabel("Username").fill("admin");
    await page.getByLabel("Password").fill("pw12345678");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByTestId("login")).toBeHidden();
    await page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/status" && r.status() === 200,
      { timeout: 15000 },
    );

    await page.getByTestId("nav-manage").click();
    await expect(page.getByTestId("account-tokens")).toBeVisible();

    const tier = page.getByTestId("account-token-tier");
    await expect(tier).toBeVisible();
    await expect(tier).toHaveValue("view"); // default read-only

    await page.getByLabel("Name").fill("e2e read-only token");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByTestId("account-token-minted")).toBeVisible();

    // the new row carries a Read-only badge
    const badge = page.getByTestId("account-token-tier-badge").filter({ hasText: "Read-only" });
    await expect(badge.first()).toBeVisible();
  });
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ems/web/frontend && npx playwright test e2e/auth.spec.ts`
Expected: FAIL — no `account-token-tier` selector / no tier badge on the minted row.

- [ ] **Step 3: Implement the selector + badge**

In `AccountTokens.tsx`:

Extend the `ApiToken` type with `tier`:

```tsx
type ApiToken = {
  id: number;
  kind: string;
  name: string | null;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  tier: string | null;
};
```

Add role + tier state and an allowed-tiers map (below the existing `useState` hooks):

```tsx
  const [role, setRole] = useState<string | null>(null);
  const [tier, setTier] = useState("view");

  // Offer only tiers <= the user's own role (server enforces this too, 400).
  const TIERS_FOR_ROLE: Record<string, { value: string; label: string }[]> = {
    reader: [{ value: "view", label: "Read-only" }],
    user: [
      { value: "view", label: "Read-only" },
      { value: "operate", label: "Operate" },
    ],
    admin: [
      { value: "view", label: "Read-only" },
      { value: "operate", label: "Operate" },
      { value: "admin", label: "Admin" },
    ],
  };
  const tierOptions = TIERS_FOR_ROLE[role ?? "reader"] ?? TIERS_FOR_ROLE.reader;
```

In `loadKind`, also capture the role:

```tsx
      setKind(typeof b.kind === "string" ? b.kind : "unknown");
      setRole(typeof b.role === "string" ? b.role : "reader");
```

Send the tier in `mint`:

```tsx
        body: JSON.stringify({ name: trimmed, tier }),
```

Add the selector inside the `admin-invite-create` block, before the Create button:

```tsx
        <label className="admin-row-field-label" htmlFor="account-token-tier">Access</label>
        <select
          id="account-token-tier"
          data-testid="account-token-tier"
          value={tier}
          disabled={minting}
          onChange={(e) => setTier(e.target.value)}
        >
          {tierOptions.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
```

Add a badge helper (above the `return`):

```tsx
  function tierBadge(t: ApiToken): string {
    if (t.kind === "session") return "Session (full account role)";
    switch (t.tier) {
      case "view": return "Read-only";
      case "operate": return "Operate";
      case "admin": return "Admin";
      default: return "Operate (legacy)"; // access token, tier IS NULL (pre-slice-5)
    }
  }
```

Render the badge in each list row's `admin-row-main` (after the name span):

```tsx
                <span className="admin-row-name">{t.name ?? "session"}</span>
                <span className="admin-row-badge" data-testid="account-token-tier-badge">
                  {tierBadge(t)}
                </span>
                <span className="admin-row-meta">created {fmtDate(t.created_at)}</span>
```

- [ ] **Step 4: Verify build + test**

Run: `cd ems/web/frontend && npx tsc --noEmit && npm run build && npx playwright test e2e/auth.spec.ts`
Expected: tsc clean; build under budget; e2e PASS.

- [ ] **Step 5: Commit**

```bash
git add ems/web/frontend/src/AccountTokens.tsx ems/web/frontend/e2e/auth.spec.ts
git commit -m "feat(auth-web): tier selector (default read-only) + tier badge in token list"
```

---

### Task 8: iOS — provision the widget token at tier `view`

**Files:**
- Modify: `ios/EMSControl/Sources/EMSControlCore/APIClient.swift` (`TokenProvisionRequest`, `provisionWidgetToken`)
- Modify: `ios/EMSControl/Sources/EMSControlCore/DashboardStore.swift` (login call — explicit `tier: "view"`)
- Modify: `ios/EMSControl/Sources/EMSControlCore/WidgetSupport.swift` (comment only)
- Test: `ios/EMSControl/Tests/EMSControlCoreTests/APIClientTests.swift`

**Interfaces:**
- Consumes: `POST /api/auth/tokens` (now accepts `tier`).
- Produces: the widget-provision request body carries `"tier":"view"`.

- [ ] **Step 1: Write the failing test**

In `APIClientTests.swift`, follow the existing mock-transport pattern (find the test that captures a request body for `provisionWidgetToken` / login). Add a test asserting the encoded body includes the tier:

```swift
func testProvisionWidgetTokenSendsViewTier() async throws {
    let transport = MockTransport(/* return a 200 with {"token":"t","name":"n"} — match the
        existing helper used by other provision tests in this file */)
    let client = APIClient(baseURL: URL(string: "http://x")!, token: "sess", transport: transport)
    _ = try await client.provisionWidgetToken(name: "iOS widget")
    let body = try XCTUnwrap(transport.lastRequest?.httpBody)
    let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
    XCTAssertEqual(json?["tier"] as? String, "view")
    XCTAssertEqual(json?["replace"] as? Bool, true)
}
```

> Match `MockTransport`'s actual initializer and the `lastRequest` accessor used by the sibling tests in this file — reuse their exact helper rather than inventing a new mock.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ios/EMSControl && swift test --filter APIClientTests.testProvisionWidgetTokenSendsViewTier`
Expected: FAIL — the request body has no `tier` key.

- [ ] **Step 3: Add the tier field**

In `APIClient.swift`, add `tier` to `TokenProvisionRequest`:

```swift
private struct TokenProvisionRequest: Encodable {
    let name: String
    let replace: Bool
    let tier: String
}
```

Give `provisionWidgetToken` a defaulted `tier` parameter and send it:

```swift
    public func provisionWidgetToken(name: String, tier: String = "view") async throws -> TokenProvisionResponse {
        var request = URLRequest(url: baseURL.appending(path: "api/auth/tokens"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try JSONEncoder.ems.encode(
            TokenProvisionRequest(name: name, replace: true, tier: tier))
        let (data, response) = try await transport.data(for: request)
        guard (200 ..< 300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
        return try JSONDecoder.ems.decode(TokenProvisionResponse.self, from: data)
    }
```

In `DashboardStore.swift`, make the login-path call explicit (line ~87):

```swift
            accessToken = try await liveClient.provisionWidgetToken(name: name, tier: "view").token
```

In `WidgetSupport.swift`, update the rationale comment to note the stored token is now read-only:

```swift
    // The token is mirrored into app-group UserDefaults (not the Keychain). As of auth slice 5 the
    // widget token is minted READ-ONLY (tier "view"), so an app-group default — on a trusted LAN,
    // over plain http:// — is an acceptable tradeoff; a shared keychain-access-group entitlement
    // is deliberately not required. (Keychain *sharing* would need that entitlement on both
    // targets; scoping the token to read-only removes the reason to add it.)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ios/EMSControl && swift test`
Expected: PASS (new test + all existing `APIClientTests`/`DashboardStoreTests`, incl. the widget write-path pinning tests).

- [ ] **Step 5: Commit**

```bash
git add ios/EMSControl/Sources/EMSControlCore/APIClient.swift ios/EMSControl/Sources/EMSControlCore/DashboardStore.swift ios/EMSControl/Sources/EMSControlCore/WidgetSupport.swift ios/EMSControl/Tests/EMSControlCoreTests/APIClientTests.swift
git commit -m "feat(auth-ios): provision widget token at tier=view (stays in app-group UserDefaults)"
```

---

## Self-Review

**Spec coverage** (against `2026-07-21-auth-slice5-hardening-design.md`):
- §3 data model (tier column + idempotent migration) → Task 1. ✅
- §4 effective-tier policy (fail-closed, min(owner,scope), legacy→operate) → Task 3 + Task 4. ✅
- §5.1 store (Principal, create/replace/onboard tier, resolve idle, construction, purge) → Tasks 2 + 5. ✅
- §5.2 authz (effective_rank, session prefixes) → Task 3. ✅
- §5.3 gate + audit is_admin → Task 4. ✅
- §5.4 mint route validation + list tier → Task 6. ✅
- §5.5 config (Config field, parse, bound, main.py wiring) → Task 5. ✅
- §6 frontend (selector default read-only ≤ own, badge from (kind,tier)) → Task 7. ✅
- §7 iOS (tier:"view", no Keychain change, comment) → Task 8. ✅
- §8 tests (migration idempotent, scope capping, legacy NULL, malformed fail-closed, session-gate with real routes, idle + disabled, mint validation, audit visibility) → distributed across Tasks 1,2,3,4,5,6. ✅

**Placeholder scan:** no TBD/TODO; every code step carries full code. The iOS test notes to reuse the file's existing `MockTransport` helper rather than invent one (the exact mock API isn't guessed) — the only deliberate deference, because the mock's constructor shape lives in that test file.

**Type/name consistency:** `effective_rank(role, kind, token_tier)`, `tier_rank`, `role_rank`, `VALID_TOKEN_TIERS`, `Principal.token_tier`, `create_token(..., tier=None)`, `replace_token(..., tier="view")`, `AuthStore(..., access_token_idle_days=)`, `Config.access_token_idle_days` — used identically across every task that references them. The tier vocabulary (`view/operate/admin`) is one constant in `authn`; the frontend badge and iOS both use `"view"`.
