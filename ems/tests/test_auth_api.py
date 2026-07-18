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
