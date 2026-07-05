"""End-to-end auth behavior for remote/VPN use (finding 7).

Covers the whole matrix the review asked for: unauthenticated reads and writes, authenticated
access, wrong-token failure, forwarded-header spoofing, and control-denial paths — for both the
default LAN posture (reads open) and the locked-down `web.require_auth` posture used over a VPN.
"""
from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _app(tmp_path, token=TOKEN):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(str(tmp_path / "ems.sqlite")),
        override_store=SettingsStore(str(tmp_path / "ems.sqlite"), table="runtime_state"),
        web_auth_token=token,
    )


def _enable_require_auth(client: TestClient) -> None:
    # require_auth is a write, so it needs the token to turn on.
    assert client.post("/api/settings", json={"web.require_auth": True},
                       headers=AUTH).status_code == 200


# --- Default LAN posture: reads open, writes gated ---------------------------------------------

def test_reads_open_by_default_writes_gated(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/api/status").status_code == 200  # read: open on the LAN
        # write: gated
        assert c.post("/api/override", json={"intent": "hold_reserve"}).status_code == 401


# --- Locked-down posture (web.require_auth = ON): everything gated ------------------------------

def test_require_auth_gates_reads_too(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        _enable_require_auth(c)
        # Unauthenticated read is now rejected — the biggest blocker for safe remote use.
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/report").status_code == 401
        assert c.get("/api/finance").status_code == 401
        # A valid token gets the mobile app in.
        assert c.get("/api/status", headers=AUTH).status_code == 200
        assert c.get("/api/report", headers=AUTH).status_code == 200


def test_require_auth_rejects_wrong_token_on_reads(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        _enable_require_auth(c)
        assert c.get("/api/status", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_writes_gated_regardless_of_require_auth(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        # Control is denied without the token whether or not reads are locked.
        assert c.post("/api/override", json={"intent": "hold_reserve"}).status_code == 401
        assert c.post("/api/settings", json={"ui.theme": "dark"}).status_code == 401
        _enable_require_auth(c)
        assert c.post("/api/override", json={"intent": "hold_reserve"}).status_code == 401
        # ...and granted with it.
        assert c.post("/api/override", json={"intent": "hold_reserve", "minutes": 30},
                      headers=AUTH).status_code == 200


def test_auth_status_endpoint_always_reachable(tmp_path):
    # The client must be able to discover that a token is required, without one.
    with TestClient(_app(tmp_path)) as c:
        _enable_require_auth(c)
        r = c.get("/api/auth")
        assert r.status_code == 200
        assert r.json() == {"required": True, "authenticated": False}


def test_health_endpoints_always_reachable(tmp_path):
    # Liveness probes must never need a token, even fully locked down.
    with TestClient(_app(tmp_path)) as c:
        _enable_require_auth(c)
        assert c.get("/health/live").status_code == 200


def test_forwarded_headers_cannot_bypass_auth(tmp_path):
    # We trust NO proxy/forwarded headers — a spoofed X-Forwarded-* must not authorise a read.
    with TestClient(_app(tmp_path)) as c:
        _enable_require_auth(c)
        spoof = {
            "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "localhost",
            "X-Real-IP": "127.0.0.1",
        }
        assert c.get("/api/status", headers=spoof).status_code == 401
        assert c.get("/api/status", headers={**spoof, **AUTH}).status_code == 200


def test_read_only_post_plan_preview_follows_read_rules(tmp_path):
    # plan-preview is a read-only what-if POST: open on the LAN, gated once reads are locked.
    with TestClient(_app(tmp_path)) as c:
        assert c.post("/api/plan-preview", json={}).status_code != 401  # open by default
        _enable_require_auth(c)
        assert c.post("/api/plan-preview", json={}).status_code == 401  # gated, no token
        assert c.post("/api/plan-preview", json={}, headers=AUTH).status_code != 401  # token ok


def test_no_token_configured_means_open_even_with_require_auth(tmp_path):
    # You can't require a credential that doesn't exist: with no token set, require_auth can't lock
    # reads. (The UI copy tells the operator to set a token first.)
    with TestClient(_app(tmp_path, token=None)) as c:
        assert c.post("/api/settings", json={"web.require_auth": True}).status_code == 200
        assert c.get("/api/status").status_code == 200
