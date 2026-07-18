"""Auth endpoints (auth slice 1, Task 8): login/logout/me/password.

POST /api/auth/login · POST /api/auth/logout · GET /api/auth/me · POST /api/auth/password.

Discovery (GET /api/auth) is NOT served here — it's the extended `auth_status` handler directly
on `app` in `ems/web/api.py` (review fix: a router route here was shadowed by that pre-existing
direct route and never reached, so the identity-mode branch was folded into `auth_status` instead
of duplicated in a second handler; see that handler's comment).

AUTH: `/api/auth/login` is listed in `ems.web.authz.EXEMPT_PATHS`, so `_AccessMiddleware` (api.py)
never sets `scope["auth_principal"]` for it. `/api/auth/logout` and `/api/auth/password` are
listed in `requires_session` (authz.py), so the Task 7 identity gate already rejects an `access`
(non-session) token with 403 before these handlers ever run — they can assume
`scope["auth_principal"]` is a session Principal.

No username-enumeration oracle: a missing OR disabled user takes the SAME branch as a wrong
password (401, generic "invalid credentials") and calls `dummy_verify()` to burn the same Argon2
work a real `verify_password` call would, so exactly one Argon2 op runs on every failure path —
no timing signal distinguishes missing/disabled/wrong-password.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ems.authn import dummy_verify, hash_password, verify_password
from ems.web.context import AppContext


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    auth_store = ctx.auth_store

    @router.post("/api/auth/login")
    async def login(request: Request, body: dict | None = None) -> JSONResponse:
        body = body or {}
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        user = await auth_store.get_user_by_username(username) if username else None
        if user is None or user["disabled"]:
            # No username enumeration + no disabled-account timing oracle: missing AND disabled
            # both burn exactly one Argon2 op via dummy_verify() before the SAME generic 401.
            dummy_verify()
            return JSONResponse({"detail": "invalid credentials"}, status_code=401)
        if not verify_password(user["password_hash"], password):
            return JSONResponse({"detail": "invalid credentials"}, status_code=401)
        raw = await auth_store.create_token(user["id"], "session")
        return JSONResponse({
            "token": raw,
            "user": {"username": user["username"], "role": user["role"]},
        })

    @router.post("/api/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        principal = request.scope.get("auth_principal")
        if principal is not None:
            await auth_store.revoke_token(principal.token_id, principal.user_id)
        return JSONResponse({"ok": True})

    @router.get("/api/auth/me")
    async def me(request: Request) -> dict:
        p = request.scope.get("auth_principal")
        return {"username": p.username, "role": p.role, "kind": p.kind}

    @router.post("/api/auth/password")
    async def change_password(request: Request, body: dict | None = None) -> JSONResponse:
        body = body or {}
        p = request.scope.get("auth_principal")
        user = await auth_store.get_user_by_id(p.user_id)
        if not verify_password(user["password_hash"], str(body.get("old", ""))):
            return JSONResponse({"detail": "invalid credentials"}, status_code=403)
        new = str(body.get("new", ""))
        if len(new) < 8:
            return JSONResponse({"detail": "password too short (min 8)"}, status_code=422)
        await auth_store.set_password(p.user_id, hash_password(new))
        return JSONResponse({"ok": True})

    return router
