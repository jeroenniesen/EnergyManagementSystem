from __future__ import annotations

from enum import IntEnum

from ems.authn import VALID_TOKEN_TIERS


class Tier(IntEnum):
    VIEW = 0
    OPERATE = 1
    ADMIN = 2


_ROLE_RANK = {"reader": 0, "user": 1, "admin": 2}

# Access-token scope ranks, aligned by position with the role ladder and Tier enum
# (view=0/VIEW, operate=1/OPERATE, admin=2/ADMIN). Single vocabulary source: ems.authn.
_TIER_RANK = {t: i for i, t in enumerate(VALID_TOKEN_TIERS)}
# Legacy access tokens (tier IS NULL, minted before slice 5) cap here — see effective_rank.
_LEGACY_ACCESS_CAP = _TIER_RANK["operate"]

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
_SESSION_ONLY_PREFIXES = ("/api/auth/tokens", "/api/users", "/api/invites")
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


def role_rank(role: str) -> int:
    return _ROLE_RANK.get(role, -1)


def tier_rank(tier: str | None) -> int:
    """Rank of an EXPLICIT tier request (mint validation). None/unknown -> -1 (invalid)."""
    return -1 if tier is None else _TIER_RANK.get(tier, -1)


def effective_rank(role: str, kind: str, token_tier: str | None) -> int:
    """The privilege rank actually granted this request. Sessions get the owner's full live role;
    access tokens get min(owner, scope). FAILS CLOSED: NULL is the legacy OPERATE cap, any unknown
    non-null tier yields rank -1 (below VIEW) so it denies everything. NEVER index _TIER_RANK
    directly here — this runs at the gate (api.py), OUTSIDE resolve()'s try/except, so a KeyError
    would surface as an uncaught HTTP 500 instead of a 403."""
    owner = _ROLE_RANK.get(role, -1)
    if kind == "session":
        return owner
    cap = _LEGACY_ACCESS_CAP if token_tier is None else _TIER_RANK.get(token_tier, -1)
    return min(owner, cap)


def required_tier(path: str, method: str) -> Tier:
    if path.startswith(_ADMIN_PREFIXES) or path in ADMIN_PATHS:
        return Tier.ADMIN
    if path in OPERATE_PATHS and method.upper() in _MUTATING_METHODS:
        return Tier.OPERATE
    return Tier.VIEW


def requires_session(path: str) -> bool:
    return path in _SESSION_ONLY_PATHS or path.startswith(_SESSION_ONLY_PREFIXES)
