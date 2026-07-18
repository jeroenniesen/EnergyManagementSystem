from __future__ import annotations

from enum import IntEnum


class Tier(IntEnum):
    VIEW = 0
    OPERATE = 1
    ADMIN = 2


_ROLE_RANK = {"reader": 0, "user": 1, "admin": 2}

# Writes any 'user' may perform (the old _WRITE_API_PATHS set).
OPERATE_PATHS = frozenset({
    "/api/override",
    "/api/settings",
    "/api/ai/validate",
    "/api/chat",
    "/api/car/soc",
    "/api/notifications/read",
})
# Mutating HTTP verbs — only these can require OPERATE on an OPERATE_PATHS member; reads (GET/
# HEAD/OPTIONS) of the same path are VIEW (mirrors api.py's own _WRITE_METHODS).
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Admin-only surfaces (prefix match) — ADMIN for every method, reads included (these surfaces are
# admin-only entirely, unlike OPERATE_PATHS where only mutations are gated).
_ADMIN_PREFIXES = ("/api/users", "/api/invites")
# Interactive-session-only surfaces (kind == 'session'); no access/machine token allowed.
_SESSION_ONLY_PATHS = frozenset({"/api/auth/password", "/api/auth/logout"})
_SESSION_ONLY_PREFIXES = ("/api/auth/tokens",)
# Reachable without any auth (login/onboard/discovery/invite-accept).
EXEMPT_PATHS = frozenset({
    "/api/auth",
    "/api/auth/login",
    "/api/auth/onboard",
})


def role_satisfies(role: str, tier: Tier) -> bool:
    return _ROLE_RANK.get(role, -1) >= int(tier)


def required_tier(path: str, method: str) -> Tier:
    if path.startswith(_ADMIN_PREFIXES):
        return Tier.ADMIN
    if path in OPERATE_PATHS and method.upper() in _MUTATING_METHODS:
        return Tier.OPERATE
    return Tier.VIEW


def requires_session(path: str) -> bool:
    return path in _SESSION_ONLY_PATHS or path.startswith(_SESSION_ONLY_PREFIXES)
