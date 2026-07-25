"""I2 (Sol #7) — a REAL uvicorn subprocess proves RESPONSE-FIRST ordering on the PRODUCTION wiring.

The unit tests spy on `request_restart`; this one exercises the actual mechanism end-to-end under
the pinned uvicorn, against the REAL `/api/system/restart` handler built by `create_app` (not a
hand-written duplicate). It asserts an ORDERED event trace:

    body_sent  →  trigger_called  →  lifespan_finally

- `body_sent`      the restart response BODY was flushed by the ASGI server (stamped by a passive
                   send-tracer wrapped OUTSIDE the production app — it does not touch the restart
                   wiring, it only observes when the `http.response.body` message is sent);
- `trigger_called` the production response-attached `BackgroundTask` ran `app.state.request_restart`
                   (Starlette runs it AFTER the body is sent — the exact contract under test);
- `lifespan_finally` uvicorn's graceful shutdown ran the production lifespan `finally` (the
                   battery-safe AUTO restore seam) — proving no `os._exit()` skipped it.

All three stamps come from the SAME server process/clock, so the ordering is DETERMINISTIC (no
cross-process clock race). The mandatory guard is `body_sent <= trigger_called`: a regression that
called the trigger BEFORE constructing/sending the response (trigger-before-body) would stamp
`trigger_called` first and fail this assertion — the ambiguity the old self-contained test could not
distinguish (a pre-body SIGTERM under *graceful* shutdown still delivers the 202, so a 202+marker
alone proves nothing about ordering). We ALSO verify the client actually received the 202 body and
that receipt precedes the lifespan shutdown.

What remains uncovered (documented honestly): the send-tracer and the lifespan wrapper are test-only
observers; the endpoint, its ADMIN+session gating, `_do_restart_trigger`, the response-attached
`BackgroundTask`, and the production lifespan are all the real `create_app` objects.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_APP_SRC = '''
import asyncio
import os
import signal
import time
from contextlib import asynccontextmanager

import uvicorn

from ems.authn import hash_password
from ems.sources.mock import MockSource
from ems.storage.audit import AuditStore
from ems.storage.auth import AuthStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

DB = os.environ["EMS_TEST_DB"]
TRACE = os.environ["EMS_TEST_TRACE"]
PORT = int(os.environ["EMS_TEST_PORT"])
USER = os.environ["EMS_TEST_USER"]
PASS = os.environ["EMS_TEST_PASS"]
RESTART_PATH = "/api/system/restart"


def _trace(event):
    # One line per event with a wall-clock nanosecond stamp; the test sorts by stamp to prove order.
    with open(TRACE, "a") as fh:
        fh.write(f"{event} {time.time_ns()}\\n")


async def _seed():
    s = AuthStore(DB)
    await s.init()
    await s.create_user(USER, hash_password(PASS), "admin")
    await s.close()


asyncio.run(_seed())

# PRODUCTION wiring: the REAL /api/system/restart handler + the REAL response-attached
# _do_restart_trigger BackgroundTask. dry_run=True → idle_for_restart always safe (driver unarmed).
app = create_app(
    MockSource(), dry_run=True, dev_mode="mock",
    settings_store=SettingsStore(DB),
    auth_store=AuthStore(DB),
    audit_store=AuditStore(DB),
)


def _trigger():
    # The injectable restart action the production trigger invokes AFTER the body is sent. Stamp the
    # moment it runs, THEN self-SIGTERM (uvicorn graceful shutdown -> production lifespan finally).
    _trace("trigger_called")
    os.kill(os.getpid(), signal.SIGTERM)


app.state.request_restart = _trigger

# Wrap the PRODUCTION lifespan so we can stamp when its shutdown `finally` finished (battery-safe
# AUTO restore + store close), WITHOUT modifying production code.
_orig_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _traced_lifespan(scope_app):
    async with _orig_lifespan(scope_app):
        yield
    _trace("lifespan_finally")


app.router.lifespan_context = _traced_lifespan


class _SendTracer:
    """Passive ASGI wrapper OUTSIDE the production app: stamps `body_sent` the instant the restart
    response body is flushed — which Starlette does BEFORE it awaits the response-attached
    background task (the trigger). It never alters the restart wiring; it only observes sends."""

    def __init__(self, inner):
        self._inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != RESTART_PATH:
            return await self._inner(scope, receive, send)

        async def _send(message):
            await send(message)
            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                _trace("body_sent")

        return await self._inner(scope, receive, _send)


if __name__ == "__main__":
    uvicorn.run(_SendTracer(app), host="127.0.0.1", port=PORT, log_level="warning")
'''


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _wait_until_up(url: str, deadline: float) -> bool:
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.1)
    return False


def _login_token(base: str, user: str, pw: str) -> str:
    data = json.dumps({"username": user, "password": pw}).encode()
    req = urllib.request.Request(
        base + "/api/auth/login", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        assert resp.status == 200, resp.status
        return json.loads(resp.read())["token"]


def _append_trace(path: Path, event: str) -> None:
    with open(path, "a") as fh:
        fh.write(f"{event} {time.time_ns()}\n")


def _read_trace(path: Path) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        name, ns = line.rsplit(" ", 1)
        out.append((name, int(ns)))
    return out


def test_restart_response_first_ordering_on_production_wiring(tmp_path):
    module = tmp_path / "instrumented_prod_app.py"
    module.write_text(_APP_SRC)
    trace = tmp_path / "trace.log"
    db = tmp_path / "ems.sqlite"
    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    # The subprocess runs `python module.py` from tmp_path — put the repo root on PYTHONPATH so
    # `import ems` resolves regardless of cwd / install mode.
    repo_root = str(Path(__file__).resolve().parents[2])
    env = {
        **os.environ,
        "EMS_SUPERVISED": "1",  # the endpoint refuses (409) when unsupervised
        "EMS_TEST_DB": str(db),
        "EMS_TEST_TRACE": str(trace),
        "EMS_TEST_PORT": str(port),
        "EMS_TEST_USER": "admin",
        "EMS_TEST_PASS": "pw12345678",
        "PYTHONPATH": repo_root + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    proc = subprocess.Popen([sys.executable, str(module)], env=env)
    try:
        assert _wait_until_up(f"{base}/health/live", time.time() + 25), (
            "instrumented production app never became reachable"
        )

        # ADMIN *session* — the restart route is ADMIN + session-only (an access token is rejected).
        token = _login_token(base, "admin", "pw12345678")
        req = urllib.request.Request(
            f"{base}/api/system/restart", data=b"{}", method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            assert resp.status == 202, resp.status  # the body was delivered before the process died
            body = resp.read()
        _append_trace(trace, "client_received_202")  # supplementary: proves delivery, same clock
        assert b"restarting" in body

        # self-SIGTERM ran after the body → uvicorn graceful shutdown → the process exits.
        exit_deadline = time.time() + 15
        while proc.poll() is None and time.time() < exit_deadline:
            time.sleep(0.05)
        assert proc.poll() is not None, "process did not exit after the restart trigger"

        events = _read_trace(trace)
        order = [name for name, _ in events]
        stamps = {name: ns for name, ns in events}

        # All three server-side events must be present — lifespan_finally proves the graceful
        # shutdown ran the production lifespan (an os._exit would skip it, dropping this marker).
        assert {"body_sent", "trigger_called", "lifespan_finally"} <= set(order), order

        # ORDERED trace (the point of this test), all on ONE process clock:
        #   body flushed  ->  trigger ran  ->  lifespan shutdown ran.
        # `body_sent <= trigger_called` is the MANDATORY response-first guard: a trigger-before-body
        # regression would stamp trigger_called first and fail here.
        assert stamps["body_sent"] <= stamps["trigger_called"], events
        assert stamps["trigger_called"] <= stamps["lifespan_finally"], events
        # The client really received the 202 before the server finished shutting down.
        assert stamps["client_received_202"] <= stamps["lifespan_finally"], events
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
