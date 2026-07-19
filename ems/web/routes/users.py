"""User-management + invite-issuance routes (auth slice 2, backend core).

GET /api/users · PATCH /api/users/{id} · DELETE /api/users/{id} ·
POST /api/invites · GET /api/invites · DELETE /api/invites/{id}.

AUTH: every path here matches `ems.web.authz._ADMIN_PREFIXES` (`/api/users`, `/api/invites`) by
prefix, for EVERY method including reads — `_AccessMiddleware` (api.py) already rejects a
non-admin before any handler below runs, so every handler can assume
`request.scope["auth_principal"]` is a resolved ADMIN `Principal`.

`POST /api/invites/accept` is the one invite-shaped endpoint NOT here — it must stay reachable
logged out, so it lives in routes/auth.py beside login/onboard (see that module's docstring) and
authz.EXEMPT_PATHS lists its exact path (checked before the `_ADMIN_PREFIXES` match would apply).

Guards (last-admin / self-demote / self-disable) are enforced by `AuthStore.set_role` /
`set_disabled` inside one `BEGIN IMMEDIATE` transaction each — this router only translates their
boolean result into HTTP status codes; it never re-implements the guard logic (SPEC §6: no
read-then-write TOCTOU here).
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ems.storage.auth import INVITE_TTL
from ems.web.context import AppContext

_ROLES = ("reader", "user", "admin")
_GUARD_DETAIL = "cannot remove or demote the last admin, or act on your own admin account"


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    auth_store = ctx.auth_store

    @router.get("/api/users")
    async def list_users_endpoint() -> dict:
        return {"users": await auth_store.list_users()}

    @router.patch("/api/users/{user_id}")
    async def patch_user(user_id: int, request: Request, body: dict | None = None) -> JSONResponse:
        body = body or {}
        if "role" not in body and "disabled" not in body:
            return JSONResponse({"detail": "role or disabled required"}, status_code=422)
        if "role" in body and body["role"] not in _ROLES:
            return JSONResponse({"detail": "invalid role"}, status_code=422)
        user = await auth_store.get_user_by_id(user_id)
        if user is None:
            return JSONResponse({"detail": "user not found"}, status_code=404)
        principal = request.scope["auth_principal"]
        if "role" in body:
            ok = await auth_store.set_role(user_id, body["role"], actor_id=principal.user_id)
            if not ok:
                return JSONResponse({"detail": _GUARD_DETAIL}, status_code=409)
        if "disabled" in body:
            ok = await auth_store.set_disabled(
                user_id, bool(body["disabled"]), actor_id=principal.user_id
            )
            if not ok:
                return JSONResponse({"detail": _GUARD_DETAIL}, status_code=409)
        return JSONResponse({"ok": True})

    @router.delete("/api/users/{user_id}")
    async def delete_user(user_id: int, request: Request) -> JSONResponse:
        user = await auth_store.get_user_by_id(user_id)
        if user is None:
            return JSONResponse({"detail": "user not found"}, status_code=404)
        principal = request.scope["auth_principal"]
        ok = await auth_store.set_disabled(user_id, True, actor_id=principal.user_id)
        if not ok:
            return JSONResponse({"detail": _GUARD_DETAIL}, status_code=409)
        return JSONResponse({"ok": True})

    @router.post("/api/invites")
    async def create_invite_endpoint(request: Request, body: dict | None = None) -> JSONResponse:
        body = body or {}
        role = body.get("role")
        if role not in _ROLES:
            return JSONResponse({"detail": "invalid role"}, status_code=422)
        principal = request.scope["auth_principal"]
        raw = await auth_store.create_invite(role, created_by=principal.user_id)
        expires_at = (datetime.now(UTC) + INVITE_TTL).isoformat()
        return JSONResponse({
            "accept_url": f"/#/accept-invite?code={raw}",
            "code": raw,
            "expires_at": expires_at,
        })

    @router.get("/api/invites")
    async def list_invites_endpoint() -> dict:
        return {"invites": await auth_store.list_invites()}

    @router.delete("/api/invites/{invite_id}")
    async def revoke_invite_endpoint(invite_id: int) -> JSONResponse:
        ok = await auth_store.revoke_invite(invite_id)
        if not ok:
            return JSONResponse({"detail": "invite not found"}, status_code=404)
        return JSONResponse({"ok": True})

    return router
