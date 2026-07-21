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


def _seed_user(db: str, username: str, password: str, role: str):
    from ems.authn import hash_password
    s = AuthStore(db)

    async def run():
        await s.init()
        await s.create_user(username, hash_password(password), role)
        await s.close()

    asyncio.run(run())


def test_auth_discovery_body_for_anonymous_caller_when_user_exists(tmp_path):
    # Review fix: /api/auth used to be shadowed by a legacy direct handler that reported
    # required:false / authenticated:true for an anonymous caller once users existed, and never
    # returned onboarding_needed/user. Assert the actual body, not just the status code, so
    # shadowing regresses loudly here instead of slipping through a status-only check.
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        body = c.get("/api/auth").json()
        assert set(body) == {
            "required", "authenticated", "onboarding_needed", "user", "shared_token_required",
        }
        assert body == {
            "required": True,
            "authenticated": False,
            "onboarding_needed": False,
            "user": None,
            # Task 9: onboarding is already closed (a user exists), so the shared-token field
            # is never needed regardless of whether a shared token is configured.
            "shared_token_required": False,
        }


def test_forced_onboarding_blocks_until_admin(tmp_path):
    db = str(tmp_path / "ems.sqlite")  # no users
    with TestClient(_app(db)) as c:
        r = c.get("/api/status")
        assert r.status_code == 409 and r.json()["detail"] == "onboarding_required"


def test_reader_forbidden_on_operate_but_can_view(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "rdr", "pw12345678", "reader")
    with TestClient(_app(db)) as c:
        login = c.post("/api/auth/login", json={"username": "rdr", "password": "pw12345678"})
        tok = login.json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert c.get("/api/status", headers=h).status_code == 200  # VIEW ok
        # Review fix: a GET on an OPERATE_PATHS member is a read — a reader must be able to view it.
        assert c.get("/api/settings", headers=h).status_code == 200
        assert c.post("/api/settings", json={"ui.theme": "dark"}, headers=h).status_code == 403


def test_user_role_can_operate(tmp_path):
    # Positive counterpart to test_reader_forbidden_on_operate_but_can_view (spec §10: the 'user'
    # role may VIEW and OPERATE, only 'admin'-only surfaces are out of reach).
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "op", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        login = c.post("/api/auth/login", json={"username": "op", "password": "pw12345678"})
        tok = login.json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert c.get("/api/settings", headers=h).status_code == 200  # VIEW ok
        assert c.post("/api/settings", json={"ui.theme": "dark"}, headers=h).status_code == 200


def test_unauthenticated_is_401_when_users_exist(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        assert c.get("/api/status").status_code == 401


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
        bad_pw = c.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        assert bad_pw.status_code == 401
        bad_user = c.post("/api/auth/login", json={"username": "ghost", "password": "x"})
        assert bad_user.status_code == 401


def test_login_disabled_user_401(tmp_path):
    # Review fix: a disabled user must be rejected with the SAME generic 401 as a bad password —
    # and (per the accompanying timing fix) go through exactly one dummy Argon2 op, not skip
    # hashing entirely, so a disabled account isn't distinguishable by response latency.
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "disabled_user", "pw12345678", "admin")

    async def _disable():
        s = AuthStore(db)
        await s.init()
        u = await s.get_user_by_username("disabled_user")
        async with s._write_conn() as conn:
            await conn.execute("UPDATE users SET disabled=1 WHERE id=?", (u["id"],))
            await conn.commit()
        await s.close()

    asyncio.run(_disable())
    with TestClient(_app(db)) as c:
        r = c.post(
            "/api/auth/login", json={"username": "disabled_user", "password": "pw12345678"}
        )
        assert r.status_code == 401
        assert r.json()["detail"] == "invalid credentials"


def test_dummy_verify_called_exactly_once_on_missing_and_disabled_user(tmp_path, monkeypatch):
    # Pins the timing-equalization invariant (module docstring) against a future short-circuit:
    # dummy_verify() must run EXACTLY once per failed request whether the username is missing or
    # disabled — never zero (that reopens a timing oracle) and never twice. Patched on
    # ems.web.routes.auth (where `from ems.authn import dummy_verify` bound the name at import
    # time) rather than on ems.authn itself, since that's the reference login() actually calls.
    import ems.web.routes.auth as auth_routes

    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "disabled_user", "pw12345678", "admin")

    async def _disable():
        s = AuthStore(db)
        await s.init()
        u = await s.get_user_by_username("disabled_user")
        async with s._write_conn() as conn:
            await conn.execute("UPDATE users SET disabled=1 WHERE id=?", (u["id"],))
            await conn.commit()
        await s.close()

    asyncio.run(_disable())

    calls = []
    monkeypatch.setattr(auth_routes, "dummy_verify", lambda: calls.append(1))

    with TestClient(_app(db)) as c:
        r1 = c.post("/api/auth/login", json={"username": "ghost", "password": "x"})
        assert r1.status_code == 401
        assert len(calls) == 1  # (a) nonexistent user — exactly one call so far

        r2 = c.post(
            "/api/auth/login", json={"username": "disabled_user", "password": "pw12345678"}
        )
        assert r2.status_code == 401
        assert len(calls) == 2  # (b) disabled user — exactly one MORE call, not two


def test_resolve_failure_is_503_not_500(tmp_path, monkeypatch):
    # Review fix: the identity gate must fail safe/deny on a transient DB error resolving the
    # token — a clean 503, never an uncaught 500 (CLAUDE.md fail-safe).
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")
    store = AuthStore(db)
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(db),
        auth_store=store,
    )

    async def _boom(_raw):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(store, "resolve", _boom)
    with TestClient(app) as c:
        r = c.get("/api/status", headers={"Authorization": "Bearer whatever"})
        assert r.status_code == 503
        assert r.json() == {"detail": "auth temporarily unavailable"}


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


# --- Slice 3: long-lived access tokens (mint/list/revoke + atomic replace) -----------------------


def _session_header(db: str, username: str, password: str) -> dict:
    with TestClient(_app(db)) as c:
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200
        return {"Authorization": f"Bearer {r.json()['token']}"}


def test_access_token_gets_403_on_tokens_mint_list_revoke(tmp_path):
    # Finding 1 / design §5: the WHOLE /api/auth/tokens* surface is interactive-session-only — an
    # access (machine) token must never be able to mint/list/revoke credentials, even its own.
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")
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
        assert c.post("/api/auth/tokens", json={"name": "x"}, headers=h).status_code == 403
        assert c.get("/api/auth/tokens", headers=h).status_code == 403
        assert c.delete("/api/auth/tokens/1", headers=h).status_code == 403


def test_token_mint_list_revoke_roundtrip(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")
    h = _session_header(db, "u", "pw12345678")
    with TestClient(_app(db)) as c:
        r = c.post("/api/auth/tokens", json={"name": "my-script"}, headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "my-script"
        raw = body["token"]

        listed = c.get("/api/auth/tokens", headers=h).json()["tokens"]
        names = {t["name"] for t in listed}
        assert "my-script" in names
        # never leaks a hash/token value in the list.
        for t in listed:
            assert "token_hash" not in t and "token" not in t
        token_id = next(t["id"] for t in listed if t["name"] == "my-script")

        # the minted raw actually works as a bearer.
        assert c.get("/api/status", headers={"Authorization": f"Bearer {raw}"}).status_code == 200

        assert c.delete(f"/api/auth/tokens/{token_id}", headers=h).status_code == 200
        assert c.get("/api/status", headers={"Authorization": f"Bearer {raw}"}).status_code == 401
        assert c.delete(f"/api/auth/tokens/{token_id}", headers=h).status_code == 404


def test_token_mint_rejects_blank_name(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")
    h = _session_header(db, "u", "pw12345678")
    with TestClient(_app(db)) as c:
        assert c.post("/api/auth/tokens", json={"name": "   "}, headers=h).status_code == 422
        assert c.post("/api/auth/tokens", json={}, headers=h).status_code == 422


def test_token_replace_atomically_revokes_and_remints_by_name(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "u", "pw12345678", "user")
    h = _session_header(db, "u", "pw12345678")
    with TestClient(_app(db)) as c:
        first = c.post(
            "/api/auth/tokens", json={"name": "iOS widget"}, headers=h
        ).json()["token"]
        second = c.post(
            "/api/auth/tokens", json={"name": "iOS widget", "replace": True}, headers=h
        ).json()["token"]
        assert c.get("/api/status",
                      headers={"Authorization": f"Bearer {first}"}).status_code == 401
        assert c.get("/api/status",
                      headers={"Authorization": f"Bearer {second}"}).status_code == 200
        listed = c.get("/api/auth/tokens", headers=h).json()["tokens"]
        assert len([t for t in listed if t["name"] == "iOS widget"]) == 1


def test_token_owner_scoping_user_b_cannot_list_or_revoke_user_a_token(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "alice", "pw12345678", "user")
    _seed_user(db, "bob", "pw12345678", "user")
    h_alice = _session_header(db, "alice", "pw12345678")
    h_bob = _session_header(db, "bob", "pw12345678")
    with TestClient(_app(db)) as c:
        minted = c.post(
            "/api/auth/tokens", json={"name": "alices-script"}, headers=h_alice
        ).json()
        assert minted["name"] == "alices-script"
        listed_alice = c.get("/api/auth/tokens", headers=h_alice).json()["tokens"]
        token_id = next(t["id"] for t in listed_alice if t["name"] == "alices-script")

        # bob's own list never contains alice's token.
        listed_bob = c.get("/api/auth/tokens", headers=h_bob).json()["tokens"]
        assert all(t["name"] != "alices-script" for t in listed_bob)

        # bob can't revoke it — 404 (no existence oracle), not 403.
        assert c.delete(f"/api/auth/tokens/{token_id}", headers=h_bob).status_code == 404
        # it's still alice's and still valid.
        listed_alice_after = c.get("/api/auth/tokens", headers=h_alice).json()["tokens"]
        assert any(t["name"] == "alices-script" for t in listed_alice_after)


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
    # auth_tokens.tier carries a DB-level CHECK(tier IS NULL OR tier IN (...)) (added in an
    # earlier task in this slice). That CHECK is a real, separate defense — but this test is
    # specifically exercising the application-layer fail-closed path (effective_rank) for a row
    # that is malformed *despite* the DB constraint (e.g. a pre-constraint legacy row, or direct
    # tooling that writes around it). Bypass the CHECK for this one INSERT to simulate that row.
    con.execute("PRAGMA ignore_check_constraints = 1")
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
