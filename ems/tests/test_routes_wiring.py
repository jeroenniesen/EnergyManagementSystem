"""Route-wiring regression net for the B-25 router split.

`web/api.py` used to define every route inside `create_app`; six self-contained domains
(car / digest / notify / export / accuracy / whatif) now live in `ems/web/routes/*` as
`build_router(ctx)` and are attached with `app.include_router(...)`. This guards the split:

1. the DIRECTLY-registered route set (everything still defined on `app` in api.py) is snapshotted
   exactly, so a route silently disappearing (or an unexpected one appearing) fails here;
2. each of the five extracted domains' routes is reachable (wired past the `/api/{rest}` 404
   catch-all) — they dispatch through the included sub-routers rather than showing up in
   `app.routes`, so they're probed over HTTP;
3. the two MOVED write paths are still gated by `_AccessMiddleware` (auth set unchanged) — proven
   functionally: with a token configured, an unauthenticated write is rejected.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.web.api import create_app

# Routes that STAY defined directly on the app in api.py (plus the health probes and the
# /api/{rest} JSON-404 catch-all). Snapshot: if this set drifts, the split changed a route.
EXPECTED_DIRECT_ROUTES = frozenset({
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/api/advisor/ev-charge"),
    ("GET", "/api/ai/validation"),
    ("GET", "/api/alerts"),
    ("GET", "/api/audit"),
    ("GET", "/api/auth"),
    ("GET", "/api/battery"),
    ("GET", "/api/battery-plan"),
    ("GET", "/api/charge-need"),
    ("GET", "/api/decision"),
    ("GET", "/api/diagnostics"),
    ("GET", "/api/energy-distribution"),
    ("GET", "/api/energy-forecast"),
    ("GET", "/api/energy-story"),
    ("GET", "/api/explainer"),
    ("GET", "/api/faq"),
    ("GET", "/api/finance"),
    ("GET", "/api/forecast"),
    ("GET", "/api/freshness"),
    ("GET", "/api/incidents"),
    ("GET", "/api/override"),
    ("GET", "/api/plan"),
    ("GET", "/api/plan-detail"),
    ("GET", "/api/prices"),
    ("GET", "/api/replay"),
    ("GET", "/api/report"),
    ("GET", "/api/savings"),
    ("GET", "/api/series"),
    ("GET", "/api/settings"),
    ("GET", "/api/sky"),
    ("GET", "/api/status"),
    ("GET", "/api/strategy"),
    ("POST", "/api/ai/validate"),
    ("POST", "/api/chat"),
    ("POST", "/api/override"),
    ("POST", "/api/plan-preview"),
    ("POST", "/api/settings"),
    # The catch-all is registered for every write method + GET.
    ("GET", "/api/{rest:path}"),
    ("POST", "/api/{rest:path}"),
    ("PUT", "/api/{rest:path}"),
    ("DELETE", "/api/{rest:path}"),
    ("PATCH", "/api/{rest:path}"),
})

# The routes the six extracted domains OWN — reachable via their included sub-routers.
EXTRACTED_GET_ROUTES = [
    "/api/cars",                       # car
    "/api/car/plan",                   # car
    "/api/digest",                     # digest
    "/api/notifications",              # notify
    "/api/export",                     # export
    "/api/export/package",             # export
    "/api/accuracy",                   # accuracy
    "/api/advisor/solar-confidence",   # accuracy
    "/api/counterfactual",             # whatif (B-69)
]
EXTRACTED_WRITE_ROUTES = [
    "/api/car/soc",                    # car
    "/api/notifications/read",         # notify
]
# POST /api/whatif (B-73) is reachable but deliberately NOT a gated write (see api.py's
# _WRITE_API_PATHS comment + ems/web/routes/whatif.py's module docstring) — checked separately
# below rather than folded into EXTRACTED_WRITE_ROUTES, whose whole point is the OPPOSITE guarantee
# (that those paths stay auth-gated).
EXTRACTED_NO_AUTH_POST_ROUTES = ["/api/whatif"]


def _direct_routes(app) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api") or path.startswith("/health"):
            for method in (getattr(route, "methods", None) or ()):
                if method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                    pairs.add((method, path))
    return pairs


def test_directly_registered_route_set_is_unchanged():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock")
    assert _direct_routes(app) == EXPECTED_DIRECT_ROUTES


def test_extracted_get_routes_are_wired():
    # Each extracted GET must be served by its router, NOT swallowed by the /api/{rest} 404.
    with TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock")) as client:
        for path in EXTRACTED_GET_ROUTES:
            resp = client.get(path)
            assert resp.status_code != 404, f"{path} fell through to the 404 catch-all"


def test_extracted_write_routes_are_wired():
    # No token → writes are open; without a store the handlers return 503 (proving the handler ran
    # and the route is not the 404 catch-all).
    with TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock")) as client:
        for path in EXTRACTED_WRITE_ROUTES:
            resp = client.post(path, json={})
            assert resp.status_code != 404, f"{path} fell through to the 404 catch-all"


def test_extracted_no_auth_post_routes_are_wired():
    # POST /api/whatif (B-73): reachable exactly like EXTRACTED_WRITE_ROUTES above (503 without a
    # store proves the handler ran), but see test_no_auth_post_routes_stay_open_with_a_token below
    # for the auth guarantee that actually distinguishes it from that set.
    with TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock")) as client:
        for path in EXTRACTED_NO_AUTH_POST_ROUTES:
            resp = client.post(path, json={})
            assert resp.status_code != 404, f"{path} fell through to the 404 catch-all"


def test_unknown_api_path_still_404s():
    # The catch-all must still guard genuinely-unknown /api paths.
    with TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock")) as client:
        assert client.get("/api/does-not-exist").status_code == 404


def test_moved_write_paths_are_gated_by_auth_middleware():
    # The moved write paths must still be in _AccessMiddleware's write set: with a token configured,
    # an unauthenticated write is rejected before the handler (401), exactly as before the split.
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", web_auth_token="s3cret")
    with TestClient(app) as client:
        for path in EXTRACTED_WRITE_ROUTES:
            assert client.post(path, json={}).status_code == 401, f"{path} not auth-gated"
            ok = client.post(path, json={}, headers={"Authorization": "Bearer s3cret"})
            assert ok.status_code != 401, f"{path} rejected a valid token"


def test_no_auth_post_routes_stay_open_with_a_token():
    # POST /api/whatif (B-73) is deliberately OUTSIDE _WRITE_API_PATHS (it's read-only by
    # construction — see api.py's comment + the whatif.py module docstring): even with a token
    # configured and no Authorization header, it must NOT 401 like the real write paths above.
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", web_auth_token="s3cret")
    with TestClient(app) as client:
        for path in EXTRACTED_NO_AUTH_POST_ROUTES:
            assert client.post(path, json={}).status_code != 401, f"{path} unexpectedly auth-gated"
