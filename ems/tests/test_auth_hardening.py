"""Slice-4 hardening: login lockout wired into the API + auth audit events (design §6/§9/§10)."""
import asyncio
import json

from fastapi.testclient import TestClient

from ems.authn import hash_password
from ems.sources.mock import MockSource
from ems.storage.audit import AuditStore
from ems.storage.auth import AuthStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


def _app(db: str, *, audit: bool = False):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(db),
        auth_store=AuthStore(db),
        audit_store=AuditStore(db) if audit else None,
    )


def _seed_user(db: str, username: str, password: str, role: str):
    s = AuthStore(db)

    async def run():
        await s.init()
        await s.create_user(username, hash_password(password), role)
        await s.close()

    asyncio.run(run())


def _auth_rows(db: str) -> list[dict]:
    a = AuditStore(db)

    async def run():
        await a.init()
        rows = await a.recent(limit=200, category="auth")
        await a.close()
        return rows

    return asyncio.run(run())


# --- A. Login lockout ---------------------------------------------------------------------------

def test_login_locks_out_after_five_failures_with_retry_after(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        for _ in range(5):  # five wrong attempts, each a generic 401
            r = c.post("/api/auth/login", json={"username": "admin", "password": "nope"})
            assert r.status_code == 401
        # The 6th is short-circuited to 429 + Retry-After BEFORE any password check.
        blocked = c.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0
        # Generic detail — no hint about the account or why.
        assert "credential" not in blocked.json()["detail"].lower()


def test_lockout_refuses_even_the_correct_password_while_locked(tmp_path):
    # The lockout check runs FIRST, so a locked account can't be unlocked by simply guessing right.
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        for _ in range(5):
            c.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        r = c.post("/api/auth/login", json={"username": "admin", "password": "pw12345678"})
        assert r.status_code == 429


def test_lockout_tracks_nonexistent_usernames_too_no_enumeration(tmp_path):
    # Tracking keys off the SUBMITTED string, so a username that doesn't exist locks exactly like a
    # real one — the 429 (like the 401 before it) reveals nothing about existence.
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db)) as c:
        for _ in range(5):
            r = c.post("/api/auth/login", json={"username": "ghost", "password": "x"})
            assert r.status_code == 401
        blocked = c.post("/api/auth/login", json={"username": "ghost", "password": "x"})
        assert blocked.status_code == 429


def test_successful_login_still_works_and_is_independent_per_user(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "alice", "pw12345678", "user")
    _seed_user(db, "bob", "pw12345678", "user")
    with TestClient(_app(db)) as c:
        for _ in range(5):  # lock ALICE out
            c.post("/api/auth/login", json={"username": "alice", "password": "nope"})
        assert c.post("/api/auth/login",
                      json={"username": "alice", "password": "pw12345678"}).status_code == 429
        # Bob is unaffected and logs in fine.
        assert c.post("/api/auth/login",
                      json={"username": "bob", "password": "pw12345678"}).status_code == 200


# --- C. Audit wiring ----------------------------------------------------------------------------

def test_login_success_and_failure_are_audited_without_secrets(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db, audit=True)) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": "nope"})  # failure
        ok = c.post("/api/auth/login", json={"username": "admin", "password": "pw12345678"})
        session = ok.json()["token"]
    rows = _auth_rows(db)
    events = [r["detail"]["event"] for r in rows]
    assert "login_success" in events
    assert "login_failure" in events
    success = next(r for r in rows if r["detail"]["event"] == "login_success")
    assert success["detail"]["username"] == "admin" and success["detail"]["role"] == "admin"
    # No secret material anywhere in the serialized auth log.
    blob = json.dumps(rows)
    assert session not in blob
    assert "pw12345678" not in blob
    assert "password_hash" not in blob and "token_hash" not in blob


def test_password_change_is_audited_as_password_changed(tmp_path):
    # Pins the event name (object_pastparticiple scheme, matching login_success/invite_accepted/
    # token_minted/etc.) — not the older "password_change".
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db, audit=True)) as c:
        session = c.post(
            "/api/auth/login", json={"username": "admin", "password": "pw12345678"}
        ).json()["token"]
        h = {"Authorization": f"Bearer {session}"}
        r = c.post("/api/auth/password", json={"old": "pw12345678", "new": "newpass123"},
                   headers=h)
        assert r.status_code == 200
    rows = _auth_rows(db)
    events = [r["detail"]["event"] for r in rows]
    assert "password_changed" in events
    assert "password_change" not in events
    changed = next(r for r in rows if r["detail"]["event"] == "password_changed")
    assert changed["detail"]["username"] == "admin"
    # No secret material anywhere in the serialized auth log.
    blob = json.dumps(rows)
    assert "pw12345678" not in blob and "newpass123" not in blob


def test_token_mint_and_invite_accept_are_audited_without_secrets(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_user(db, "admin", "pw12345678", "admin")
    with TestClient(_app(db, audit=True)) as c:
        session = c.post(
            "/api/auth/login", json={"username": "admin", "password": "pw12345678"}
        ).json()["token"]
        h = {"Authorization": f"Bearer {session}"}
        minted = c.post("/api/auth/tokens", json={"name": "my-script"}, headers=h).json()["token"]
        code = c.post("/api/invites", json={"role": "user"}, headers=h).json()["code"]
        accepted = c.post(
            "/api/invites/accept",
            json={"code": code, "username": "newbie", "password": "pw12345678"},
        ).json()["token"]
    rows = _auth_rows(db)
    events = [r["detail"]["event"] for r in rows]
    assert "token_minted" in events
    assert "invite_created" in events
    assert "invite_accepted" in events
    minted_row = next(r for r in rows if r["detail"]["event"] == "token_minted")
    assert minted_row["detail"]["token_name"] == "my-script"  # NAME only, never the raw token
    accept_row = next(r for r in rows if r["detail"]["event"] == "invite_accepted")
    assert accept_row["detail"]["username"] == "newbie" and accept_row["detail"]["role"] == "user"
    # No raw token, invite code, session token, or password reaches the audit log (the token NAME
    # "my-script" IS present by design — that is metadata, not a credential).
    blob = json.dumps(rows)
    for secret in (minted, code, accepted, "pw12345678"):
        assert secret not in blob
    assert "token_hash" not in blob and "password_hash" not in blob
