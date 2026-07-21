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
# Admin-only EXACT paths (not prefix) — P2 security review: the support/diagnostics bundle bundles
# the FULL audit trail (every "auth"-category row: usernames, roles, login failures, lockouts,
# role changes, invites, token mint/revoke) plus the server-log tail, so it must never be reachable
# below ADMIN even though it doesn't live under `_ADMIN_PREFIXES`. `/api/audit` deliberately stays
# OUT of this set — reader/user roles legitimately use the Manage → Audit view for transparency
# into decisions/config/overrides; that endpoint instead strips "auth"-category rows for non-admins
# in the handler itself (ems/web/api.py's `audit_endpoint`) rather than losing the whole surface.
ADMIN_PATHS = frozenset({"/api/export/package"})
# Interactive-session-only surfaces (kind == 'session'); no access/machine token allowed.
_SESSION_ONLY_PATHS = frozenset({"/api/auth/password", "/api/auth/logout"})
_SESSION_ONLY_PREFIXES = ("/api/auth/tokens",)
# Reachable without any auth (login/onboard/discovery/invite-accept). NOTE: `/api/invites/accept`
# would otherwise fall under `_ADMIN_PREFIXES` (it starts with "/api/invites") — but the identity
# gate in api.py checks `path not in EXEMPT_PATHS` BEFORE consulting `required_tier`, so listing
# the exact path here is what keeps it reachable logged-out despite the prefix match.
EXEMPT_PATHS = frozenset({
    "/api/auth",
    "/api/auth/login",
    "/api/auth/onboard",
    "/api/invites/accept",
})


def role_satisfies(role: str, tier: Tier) -> bool:
    return _ROLE_RANK.get(role, -1) >= int(tier)


def required_tier(path: str, method: str) -> Tier:
    if path.startswith(_ADMIN_PREFIXES) or path in ADMIN_PATHS:
        return Tier.ADMIN
    if path in OPERATE_PATHS and method.upper() in _MUTATING_METHODS:
        return Tier.OPERATE
    return Tier.VIEW


def requires_session(path: str) -> bool:
    return path in _SESSION_ONLY_PATHS or path.startswith(_SESSION_ONLY_PREFIXES)
