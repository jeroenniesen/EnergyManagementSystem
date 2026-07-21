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


def _seed_user(db: str, username: str, password: str, role: str) -> int:
    from ems.authn import hash_password
    s = AuthStore(db)
    uid = {}

    async def run():
        await s.init()
        uid["id"] = await s.create_user(username, hash_password(password), role)
        await s.close()

    asyncio.run(run())
    return uid["id"]


def _login(c: TestClient, username: str, password: str) -> str:
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# --- Tier gating -----------------------------------------------------------------------------


def test_reader_and_user_get_403_on_users_and_invites(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    _seed_user(db, "rdr", "pw12345678", "reader")
    _seed_user(db, "usr", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        for username in ("rdr", "usr"):
            tok = _login(c, username, "pw12345678")
            h = _auth(tok)
            assert c.get("/api/users", headers=h).status_code == 403
            assert c.get("/api/invites", headers=h).status_code == 403
            assert c.post("/api/invites", json={"role": "user"}, headers=h).status_code == 403


def test_admin_gets_200_on_users_and_invites(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        tok = _login(c, "admin", "pw12345678")
        h = _auth(tok)
        assert c.get("/api/users", headers=h).status_code == 200
        assert c.get("/api/invites", headers=h).status_code == 200


def test_unauthenticated_gets_401_on_users_and_invites(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        assert c.get("/api/users").status_code == 401
        assert c.get("/api/invites").status_code == 401


# --- Full invite flow --------------------------------------------------------------------------


def test_full_invite_flow_admin_creates_anonymous_accepts_new_user_logs_in(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        r = c.post("/api/invites", json={"role": "user"}, headers=_auth(admin_tok))
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"accept_url", "code", "expires_at"}
        assert body["accept_url"] == f"/#/accept-invite?code={body['code']}"

        # anonymous — no Authorization header at all — can reach the accept endpoint
        accept = c.post("/api/invites/accept", json={
            "code": body["code"], "username": "newbie", "password": "pw12345678",
        })
        assert accept.status_code == 200, accept.text
        assert accept.json()["user"] == {"username": "newbie", "role": "user"}
        new_tok = accept.json()["token"]

        # the new user can log in with the invite's role
        login = c.post("/api/auth/login", json={"username": "newbie", "password": "pw12345678"})
        assert login.status_code == 200
        assert login.json()["user"]["role"] == "user"

        # the invite-minted token itself already works
        assert c.get("/api/auth/me", headers=_auth(new_tok)).json()["role"] == "user"

        # invite now shows as used
        invites = c.get("/api/invites", headers=_auth(admin_tok)).json()["invites"]
        assert invites[0]["used_at"] is not None


def test_invite_accept_reachable_while_logged_out_with_bad_code_is_401_not_403(tmp_path):
    # The specific EXEMPT-vs-ADMIN-prefix regression this iteration must guard: an unauthenticated
    # POST to /api/invites/accept with a garbage code must get the invite-specific 401, never the
    # 403 an ADMIN-tier path would give an anonymous caller once users exist (well, that would
    # actually be 401 unauth too — the real risk is 409 "onboarding_required" or a tier 403 if the
    # exemption wiring regresses), proving EXEMPT_PATHS wins over the /api/invites prefix match.
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        r = c.post("/api/invites/accept", json={
            "code": "garbage", "username": "x", "password": "pw12345678",
        })
        assert r.status_code == 401
        assert r.json()["detail"] == "invalid invite"


def test_invite_accept_used_expired_garbage_code_all_401(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    admin_id = _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        # garbage
        r = c.post("/api/invites/accept",
                    json={"code": "nope", "username": "a", "password": "pw12345678"})
        assert r.status_code == 401

        # used
        create = c.post("/api/invites", json={"role": "user"}, headers=_auth(admin_tok))
        code = create.json()["code"]
        first = c.post("/api/invites/accept",
                        json={"code": code, "username": "once", "password": "pw12345678"})
        assert first.status_code == 200
        second = c.post("/api/invites/accept",
                         json={"code": code, "username": "twice", "password": "pw12345678"})
        assert second.status_code == 401

    # expired — construct directly via the store (admin_id already created above)
    async def _expired_code():
        from datetime import UTC, datetime, timedelta

        from ems.authn import hash_token, new_token
        s = AuthStore(db)
        await s.init()
        raw = new_token()
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        async with s._write_conn() as conn:
            await conn.execute(
                "INSERT INTO invites (token_hash, role, created_by, created_at, expires_at) "
                "VALUES (?,?,?,?,?)",
                (hash_token(raw), "user", admin_id, past, past),
            )
            await conn.commit()
        await s.close()
        return raw

    expired_raw = asyncio.run(_expired_code())
    with TestClient(_app(db)) as c:
        r = c.post("/api/invites/accept",
                    json={"code": expired_raw, "username": "late", "password": "pw12345678"})
        assert r.status_code == 401


def test_invite_accept_username_collision_409(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    _seed_user(db, "taken", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        create = c.post("/api/invites", json={"role": "user"}, headers=_auth(admin_tok))
        code = create.json()["code"]
        r = c.post("/api/invites/accept",
                    json={"code": code, "username": "taken", "password": "pw12345678"})
        assert r.status_code == 409


def test_invite_accept_validates_username_and_password(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        code = c.post("/api/invites", json={"role": "user"},
                       headers=_auth(admin_tok)).json()["code"]
        # blank/whitespace-only username (after strip) -> 422
        r = c.post("/api/invites/accept",
                    json={"code": code, "username": "   ", "password": "pw12345678"})
        assert r.status_code == 422
        # short password -> 422
        r2 = c.post("/api/invites/accept",
                     json={"code": code, "username": "ok", "password": "short"})
        assert r2.status_code == 422


def test_invite_create_invalid_role_422(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        r = c.post("/api/invites", json={"role": "superuser"}, headers=_auth(admin_tok))
        assert r.status_code == 422


def test_revoke_invite(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        create = c.post("/api/invites", json={"role": "user"}, headers=_auth(admin_tok))
        invite_id = c.get("/api/invites", headers=_auth(admin_tok)).json()["invites"][0]["id"]
        assert c.delete(f"/api/invites/{invite_id}", headers=_auth(admin_tok)).status_code == 200
        assert c.delete(f"/api/invites/{invite_id}", headers=_auth(admin_tok)).status_code == 404
        # revoked invite can no longer be accepted
        r = c.post("/api/invites/accept", json={
            "code": create.json()["code"], "username": "x", "password": "pw12345678",
        })
        assert r.status_code == 401


# --- PATCH / DELETE /api/users/{id} -------------------------------------------------------------


def test_patch_user_role_and_disable_happy_path(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    uid = _seed_user(db, "plain", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        h = _auth(admin_tok)
        r = c.patch(f"/api/users/{uid}", json={"role": "reader"}, headers=h)
        assert r.status_code == 200
        users = c.get("/api/users", headers=h).json()["users"]
        assert next(u for u in users if u["id"] == uid)["role"] == "reader"

        r2 = c.patch(f"/api/users/{uid}", json={"disabled": True}, headers=h)
        assert r2.status_code == 200
        users2 = c.get("/api/users", headers=h).json()["users"]
        assert next(u for u in users2 if u["id"] == uid)["disabled"] is True or \
            next(u for u in users2 if u["id"] == uid)["disabled"] == 1


def test_patch_user_unknown_user_404(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        r = c.patch("/api/users/999999", json={"role": "user"}, headers=_auth(admin_tok))
        assert r.status_code == 404


def test_patch_user_invalid_role_422(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    uid = _seed_user(db, "plain", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        r = c.patch(f"/api/users/{uid}", json={"role": "bogus"}, headers=_auth(admin_tok))
        assert r.status_code == 422


def test_patch_user_last_admin_and_self_demote_409(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    admin_id = _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        h = _auth(admin_tok)
        # sole admin cannot demote themselves
        r = c.patch(f"/api/users/{admin_id}", json={"role": "user"}, headers=h)
        assert r.status_code == 409
        # sole admin cannot disable themselves either
        r2 = c.delete(f"/api/users/{admin_id}", headers=h)
        assert r2.status_code == 409


def test_delete_user_soft_disables_and_revokes_tokens(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    uid = _seed_user(db, "plain", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        plain_tok = _login(c, "plain", "pw12345678")
        assert c.get("/api/auth/me", headers=_auth(plain_tok)).status_code == 200
        r = c.delete(f"/api/users/{uid}", headers=_auth(admin_tok))
        assert r.status_code == 200
        # soft-disabled, not hard-deleted
        users = c.get("/api/users", headers=_auth(admin_tok)).json()["users"]
        row = next(u for u in users if u["id"] == uid)
        assert row["disabled"] in (True, 1)
        # session revoked immediately
        assert c.get("/api/auth/me", headers=_auth(plain_tok)).status_code == 401


def test_delete_user_unknown_404(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        admin_tok = _login(c, "admin", "pw12345678")
        assert c.delete("/api/users/999999", headers=_auth(admin_tok)).status_code == 404
