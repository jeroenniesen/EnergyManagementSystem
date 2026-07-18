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
