"""Auth endpoints (auth slice 1, Tasks 8-9; invite-accept added in slice 2; long-lived access
tokens added in slice 3): onboard/login/logout/me/password/invite-accept/tokens.

POST /api/auth/onboard · POST /api/auth/login · POST /api/auth/logout · GET /api/auth/me ·
POST /api/auth/password · POST /api/invites/accept ·
POST /api/auth/tokens · GET /api/auth/tokens · DELETE /api/auth/tokens/{id}.

Tokens (design §5/§7): every `/api/auth/tokens*` path is BOTH `Tier.VIEW` (any role manages its
OWN tokens — `authz.required_tier` falls through to VIEW since it matches neither `_ADMIN_PREFIXES`
nor `OPERATE_PATHS`) AND `requires_session` (`authz._SESSION_ONLY_PREFIXES`), so
`_AccessMiddleware` already rejects an access/machine-token caller with 403 before any handler
below runs — every handler here can assume `scope["auth_principal"]` is a resolved, session-kind
Principal. All three are owner-scoped by construction: `list_tokens`/`revoke_token` take
`principal.user_id`, never a caller-supplied one, so there is no IDOR to defend against here (a
foreign token id 404s — see `revoke_token`'s `WHERE id=? AND user_id=?` — never 403, so a caller
can't use the status code to probe whether an id exists for someone else).

`POST /api/invites/accept` lives HERE (not routes/users.py) even though it is invite-shaped,
because it must be reachable while logged out — it belongs with the other EXEMPT auth flows
(login/onboard), not the ADMIN-tier invite-management surface in routes/users.py.

Discovery (GET /api/auth) is NOT served here — it's the extended `auth_status` handler directly
on `app` in `ems/web/api.py` (review fix: a router route here was shadowed by that pre-existing
direct route and never reached, so the identity-mode branch was folded into `auth_status` instead
of duplicated in a second handler; see that handler's comment). That handler reports
`shared_token_required` (Task 9) so the client knows whether the onboarding form must collect the
legacy shared token.

AUTH: `/api/auth/login` and `/api/auth/onboard` are listed in `ems.web.authz.EXEMPT_PATHS`, so
`_AccessMiddleware` (api.py) never sets `scope["auth_principal"]` for them. `/api/auth/logout` and
`/api/auth/password` are listed in `requires_session` (authz.py), so the Task 7 identity gate
already rejects an `access` (non-session) token with 403 before these handlers ever run — they can
assume `scope["auth_principal"]` is a session Principal.

No username-enumeration oracle: a missing OR disabled user takes the SAME branch as a wrong
password (401, generic "invalid credentials") and calls `dummy_verify()` to burn the same Argon2
work a real `verify_password` call would, so exactly one Argon2 op runs on every failure path —
no timing signal distinguishes missing/disabled/wrong-password.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ems.authn import dummy_verify, hash_password, hash_token, verify_password
from ems.storage.auth import UsernameTaken
from ems.web.context import AppContext
from ems.web.ratelimit import LoginRateLimiter


def _validate_new_account(username: str, password: str) -> tuple[str, JSONResponse | None]:
    """Shared new-account validation for onboard + accept_invite: strip the username FIRST (a
    whitespace-only submission must not slip past the length check — that was the bug this helper
    fixes for onboard), then require a non-empty username and an 8+ char password, both behind the
    SAME generic 422 message the two callers already used independently. Returns the stripped
    username (callers must use this value, not their own unstripped copy) plus an error response
    or None."""
    stripped = username.strip()
    if len(stripped) < 1 or len(password) < 8:
        return stripped, JSONResponse(
            {"detail": "username required; password min 8"}, status_code=422)
    return stripped, None


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    auth_store = ctx.auth_store
    # One in-process login limiter per app (design §9). Single-process is sufficient at single-home
    # scale; only login is rate-limited (invite codes are 256-bit, onboarding is one-shot — neither
    # is brute-forceable, so neither needs this).
    limiter = LoginRateLimiter()

    async def _record_failure(username: str) -> None:
        # Record the failed attempt and audit it — plus a distinct lockout event exactly once, on
        # the attempt that trips the limit (register_failure returns True only then). The audit row
        # for a missing vs. real user is identical (submitted username only, no user_id), so the
        # audit log itself carries no enumeration oracle.
        tripped = limiter.register_failure(username)
        await ctx.audit_auth("login_failure", f"Failed login: {username or '<blank>'}",
                             username=username)
        if tripped:
            await ctx.audit_auth(
                "login_lockout",
                f"Account locked after repeated failed logins: {username or '<blank>'}",
                username=username)

    @router.post("/api/auth/login")
    async def login(request: Request, body: dict | None = None) -> JSONResponse:
        body = body or {}
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        # Anti-abuse (design §9): a locked username short-circuits to 429 + Retry-After BEFORE any
        # DB/Argon2 work. Generic detail; tracking keys off the SUBMITTED string, so a missing user
        # locks exactly like a real one — no enumeration and no timing signal from the lockout.
        # We do NOT audit each blocked attempt (the lockout was already recorded once when it
        # tripped — see `_record_failure`); auditing every retry would let an attacker flood it.
        retry = limiter.retry_after(username)
        if retry is not None:
            return JSONResponse(
                {"detail": "too many failed attempts; try again later"},
                status_code=429, headers={"Retry-After": str(retry)},
            )
        user = await auth_store.get_user_by_username(username) if username else None
        if user is None or user["disabled"]:
            # No username enumeration + no disabled-account timing oracle: missing AND disabled
            # both burn exactly one Argon2 op via dummy_verify() before the SAME generic 401.
            dummy_verify()
            await _record_failure(username)
            return JSONResponse({"detail": "invalid credentials"}, status_code=401)
        if not verify_password(user["password_hash"], password):
            await _record_failure(username)
            return JSONResponse({"detail": "invalid credentials"}, status_code=401)
        limiter.reset(username)
        raw = await auth_store.create_token(user["id"], "session")
        await ctx.audit_auth("login_success", f"Login: {user['username']} ({user['role']})",
                             username=user["username"], user_id=user["id"], role=user["role"])
        return JSONResponse({
            "token": raw,
            "user": {"username": user["username"], "role": user["role"]},
        })

    @router.post("/api/auth/onboard")
    async def onboard(request: Request, body: dict | None = None) -> JSONResponse:
        """Create the first admin. User management/invites (routes/users.py) and invite-accept
        (below, in this module) shipped in slice 2 — this handler stays onboarding-only.

        Anti-seizure: when a shared token (`EMS_WEB_TOKEN` / UI-set `web.auth_token`) is already
        configured, onboarding REQUIRES proving control of it (else anyone reaching this exempt
        endpoint before the real operator could mint themselves the first admin). The shared
        token is migrated into an `access` token owned by the new admin, in the SAME atomic
        transaction as the admin + session insert (`AuthStore.onboard_admin`).
        """
        if request.app.state.users_exist:
            return JSONResponse({"detail": "already onboarded"}, status_code=409)
        body = body or {}
        username, err = _validate_new_account(
            str(body.get("username", "")), str(body.get("password", "")))
        if err is not None:
            return err
        password = str(body.get("password", ""))
        shared = ctx.effective_web_token()
        migrate_hash = None
        if shared is not None:  # anti-seizure: prove control of the existing shared token
            if not secrets.compare_digest(str(body.get("shared_token", "")), shared):
                return JSONResponse({"detail": "shared token required"}, status_code=403)
            migrate_hash = hash_token(shared)
        result = await auth_store.onboard_admin(username, hash_password(password),
                                                migrate_token_hash=migrate_hash)
        if result is None:
            return JSONResponse({"detail": "already onboarded"}, status_code=409)
        _uid, raw = result
        request.app.state.users_exist = True
        await ctx.audit_auth("onboard", f"Onboarded first admin: {username}",
                             username=username, user_id=_uid, role="admin",
                             shared_token_migrated=migrate_hash is not None)
        return JSONResponse({"token": raw, "user": {"username": username, "role": "admin"}})

    @router.post("/api/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        principal = request.scope.get("auth_principal")
        if principal is not None:
            await auth_store.revoke_token(principal.token_id, principal.user_id)
            await ctx.audit_auth("logout", f"Logout: {principal.username}",
                                 username=principal.username, user_id=principal.user_id)
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
        await ctx.audit_auth("password_changed", f"Password changed: {p.username}",
                             username=p.username, user_id=p.user_id)
        return JSONResponse({"ok": True})

    @router.post("/api/invites/accept")
    async def accept_invite(request: Request, body: dict | None = None) -> JSONResponse:
        """EXEMPT (authz.EXEMPT_PATHS) — reachable logged out, like login/onboard. Generic 401
        "invalid invite" for unknown/expired/already-used codes (no oracle on which); a 409 for a
        valid invite whose requested username collides (the invite itself is still usable — see
        `AuthStore.accept_invite`/`UsernameTaken`)."""
        body = body or {}
        code = str(body.get("code", ""))
        username, err = _validate_new_account(
            str(body.get("username", "")), str(body.get("password", "")))
        if err is not None:
            return err
        password = str(body.get("password", ""))
        try:
            result = await auth_store.accept_invite(code, username, hash_password(password))
        except UsernameTaken:
            return JSONResponse({"detail": "username already taken"}, status_code=409)
        if result is None:
            return JSONResponse({"detail": "invalid invite"}, status_code=401)
        uid, raw = result
        user = await auth_store.get_user_by_id(uid)
        await ctx.audit_auth("invite_accepted",
                             f"Account created via invite: {user['username']} ({user['role']})",
                             username=user["username"], user_id=uid, role=user["role"])
        return JSONResponse({"token": raw, "user": {"username": user["username"],
                                                     "role": user["role"]}})

    @router.post("/api/auth/tokens")
    async def create_token_endpoint(request: Request, body: dict | None = None) -> JSONResponse:
        """Mint (or, with `replace: true`, atomically revoke-and-remint by name — see
        `AuthStore.replace_token`) a long-lived `access` token owned by the caller. The raw value
        is returned exactly once here; only its hash is ever stored (`GET /api/auth/tokens` below
        never exposes it again)."""
        body = body or {}
        principal = request.scope["auth_principal"]
        name = str(body.get("name", "")).strip()
        if not name:
            return JSONResponse({"detail": "name required"}, status_code=422)
        if body.get("replace"):
            raw = await auth_store.replace_token(principal.user_id, name)
            await ctx.audit_auth("token_replaced", f"Access token replaced: {name}",
                                 username=principal.username, user_id=principal.user_id,
                                 token_name=name)
        else:
            raw = await auth_store.create_token(principal.user_id, "access", name=name)
            await ctx.audit_auth("token_minted", f"Access token minted: {name}",
                                 username=principal.username, user_id=principal.user_id,
                                 token_name=name)
        return JSONResponse({"token": raw, "name": name})

    @router.get("/api/auth/tokens")
    async def list_tokens_endpoint(request: Request) -> dict:
        """OWN tokens only — never hashes. Includes the caller's current session row too (it's in
        the same table as machine tokens by design, §3); rendered like any other row, nothing
        special marked."""
        principal = request.scope["auth_principal"]
        return {"tokens": await auth_store.list_tokens(principal.user_id)}

    @router.delete("/api/auth/tokens/{token_id}")
    async def revoke_token_endpoint(token_id: int, request: Request) -> JSONResponse:
        principal = request.scope["auth_principal"]
        ok = await auth_store.revoke_token(token_id, principal.user_id)
        if not ok:
            return JSONResponse({"detail": "token not found"}, status_code=404)
        await ctx.audit_auth("token_revoked", f"Access token revoked (#{token_id})",
                             username=principal.username, user_id=principal.user_id,
                             token_id=token_id)
        return JSONResponse({"ok": True})

    return router
