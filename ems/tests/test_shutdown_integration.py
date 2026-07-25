"""I2 (Sol #7) — a REAL uvicorn subprocess proves the response-before-exit ordering.

The unit tests spy on `request_restart`; this one exercises the actual mechanism end-to-end: a
harmless instrumented app under the pinned uvicorn whose `request_restart` is a real self-SIGTERM
and whose lifespan writes a marker file on shutdown. We POST the restart, and assert:
  1. the 202 body is delivered (the response is sent BEFORE the process dies), and
  2. the shutdown marker exists (uvicorn's graceful shutdown ran the lifespan `finally`), and
  3. the process actually exits.

This is what a naive `os._exit()` would break (no lifespan cleanup) and what a
before-response exit would break (no 202 delivered).
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

_APP_SRC = '''
import os
import signal
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

MARKER = os.environ["EMS_TEST_MARKER"]
PORT = int(os.environ["EMS_TEST_PORT"])


@asynccontextmanager
async def lifespan(app):
    yield
    # Shutdown `finally` — the exact seam the real EMS uses for its battery-safe AUTO restore.
    with open(MARKER, "w") as fh:
        fh.write("shutdown-ran")


app = FastAPI(lifespan=lifespan)


def _self_sigterm():
    os.kill(os.getpid(), signal.SIGTERM)


app.state.request_restart = _self_sigterm


@app.get("/health/live")
def live():
    return {"status": "alive"}


@app.post("/api/system/restart")
async def restart():
    # Response-attached background task: Starlette sends the body, THEN awaits the task.
    return JSONResponse(
        {"restarting": True},
        status_code=202,
        background=BackgroundTask(app.state.request_restart),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
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


def test_restart_delivers_202_then_runs_lifespan_and_exits(tmp_path):
    module = tmp_path / "instrumented_app.py"
    module.write_text(_APP_SRC)
    marker = tmp_path / "shutdown.marker"
    port = _free_port()

    env = {
        "EMS_TEST_MARKER": str(marker),
        "EMS_TEST_PORT": str(port),
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    proc = subprocess.Popen([sys.executable, str(module)], env=env)
    try:
        assert _wait_until_up(f"http://127.0.0.1:{port}/health/live", time.time() + 20), (
            "instrumented app never became reachable"
        )

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/restart", data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            assert resp.status == 202  # the body was delivered before the process died
            assert b"restarting" in resp.read()

        # The self-SIGTERM ran after the body; uvicorn's graceful shutdown must run the lifespan.
        exit_deadline = time.time() + 15
        while proc.poll() is None and time.time() < exit_deadline:
            time.sleep(0.05)
        assert proc.poll() is not None, "process did not exit after the restart trigger"
        assert marker.exists(), "lifespan shutdown `finally` did not run (marker missing)"
        assert marker.read_text() == "shutdown-ran"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
