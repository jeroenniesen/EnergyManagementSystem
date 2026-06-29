from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


def _app(tmp_path, token):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(str(tmp_path / "ems.sqlite")),
        override_store=SettingsStore(str(tmp_path / "ems.sqlite"), table="runtime_state"),
        web_auth_token=token,
    )


def test_auth_status_reports_open_when_no_token():
    c = TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock"))
    b = c.get("/api/auth").json()
    assert b == {"required": False, "authenticated": True}


def test_writes_open_when_no_token_configured(tmp_path):
    with TestClient(_app(tmp_path, token=None)) as c:
        assert c.post("/api/settings", json={"ui.theme": "dark"}).status_code == 200


def test_auth_status_reports_required_with_token(tmp_path):
    with TestClient(_app(tmp_path, token="s3cret")) as c:
        anon = c.get("/api/auth").json()
        assert anon == {"required": True, "authenticated": False}
        ok = c.get("/api/auth", headers={"Authorization": "Bearer s3cret"}).json()
        assert ok == {"required": True, "authenticated": True}


def test_writes_require_token_when_configured(tmp_path):
    with TestClient(_app(tmp_path, token="s3cret")) as c:
        assert c.post("/api/settings", json={"ui.theme": "dark"}).status_code == 401
        assert c.post("/api/settings", json={"ui.theme": "dark"},
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.post("/api/settings", json={"ui.theme": "dark"},
                    headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
        assert ok.json()["values"]["ui.theme"] == "dark"


def test_override_write_requires_token_when_configured(tmp_path):
    with TestClient(_app(tmp_path, token="s3cret")) as c:
        assert c.post("/api/override", json={"intent": "hold_reserve"}).status_code == 401
        ok = c.post("/api/override", json={"intent": "hold_reserve", "minutes": 30},
                    headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


def test_non_ascii_configured_token_is_clean_401_not_500(tmp_path):
    # If an operator sets a non-ASCII EMS_WEB_TOKEN, secrets.compare_digest raises TypeError
    # against an ASCII client token. The guard must fail closed (401), never surface a 500.
    with TestClient(_app(tmp_path, token="ünïcödé")) as c:
        r = c.post("/api/settings", json={"ui.theme": "dark"},
                   headers={"Authorization": "Bearer guess"})
        assert r.status_code == 401


def test_web_token_can_be_set_from_the_ui_and_then_gates_writes(tmp_path):
    # Every credential is UI-settable: with NO env token, the operator sets web.auth_token via
    # /api/settings (allowed because access is still open), and from then on writes need it.
    with TestClient(_app(tmp_path, token=None)) as c:
        assert c.get("/api/auth").json()["required"] is False
        # Set the token through the normal settings write (open at this point).
        assert c.post("/api/settings", json={"web.auth_token": "lan-secret"}).status_code == 200
        # Now access is required, sourced from the UI setting (no restart, no env).
        assert c.get("/api/auth").json()["required"] is True
        assert c.post("/api/settings", json={"ui.theme": "dark"}).status_code == 401
        ok = c.post("/api/settings", json={"ui.theme": "dark"},
                    headers={"Authorization": "Bearer lan-secret"})
        assert ok.status_code == 200


def test_ui_token_overrides_env_token(tmp_path):
    # The UI-set token takes precedence over the EMS_WEB_TOKEN env seed.
    with TestClient(_app(tmp_path, token="env-token")) as c:
        c.post("/api/settings", json={"web.auth_token": "ui-token"},
               headers={"Authorization": "Bearer env-token"})
        # The env token no longer works; the UI one does.
        assert c.post("/api/settings", json={"ui.theme": "dark"},
                      headers={"Authorization": "Bearer env-token"}).status_code == 401
        assert c.post("/api/settings", json={"ui.theme": "dark"},
                      headers={"Authorization": "Bearer ui-token"}).status_code == 200


def test_reads_are_open_even_with_token(tmp_path):
    # The dashboard (reads) must work for a guest with no token (SPEC: degrade to read-only).
    with TestClient(_app(tmp_path, token="s3cret")) as c:
        assert c.get("/api/status").status_code == 200
        assert c.get("/api/settings").status_code == 200
        assert c.get("/api/charge-need").status_code == 200
