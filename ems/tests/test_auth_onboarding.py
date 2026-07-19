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
        r2 = c.get("/api/status", headers={"Authorization": f"Bearer {tok}"})
        assert r2.status_code == 200
        # onboarding now closed
        r3 = c.post("/api/auth/onboard", json={"username": "x", "password": "yyyyyyyy"})
        assert r3.status_code == 409


def test_onboard_requires_shared_token_and_migrates_it(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(_app(db, token="legacy-shared")) as c:
        no_token = c.post("/api/auth/onboard",
                          json={"username": "a", "password": "pw12345678"})
        assert no_token.status_code == 403
        r = c.post("/api/auth/onboard", json={
            "username": "a", "password": "pw12345678", "shared_token": "legacy-shared",
        })
        assert r.status_code == 200
        # the old shared token now works as a migrated access token
        migrated = c.get("/api/status", headers={"Authorization": "Bearer legacy-shared"})
        assert migrated.status_code == 200


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
