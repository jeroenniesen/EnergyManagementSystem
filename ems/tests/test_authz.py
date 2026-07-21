from ems.web.authz import (
    Tier,
    effective_rank,
    required_tier,
    requires_session,
    role_rank,
    role_satisfies,
    tier_rank,
)


def test_role_satisfies():
    assert role_satisfies("reader", Tier.VIEW)
    assert not role_satisfies("reader", Tier.OPERATE)
    assert role_satisfies("user", Tier.OPERATE)
    assert not role_satisfies("user", Tier.ADMIN)
    assert role_satisfies("admin", Tier.ADMIN)
    assert not role_satisfies("bogus", Tier.VIEW)


def test_required_tier():
    assert required_tier("/api/status", "GET") == Tier.VIEW
    assert required_tier("/api/settings", "POST") == Tier.OPERATE
    # Review fix: a GET on an OPERATE_PATHS member is a read — readers must not be 403'd on it.
    assert required_tier("/api/settings", "GET") == Tier.VIEW
    assert required_tier("/api/override", "GET") == Tier.VIEW
    assert required_tier("/api/users", "GET") == Tier.ADMIN
    assert required_tier("/api/users/5", "DELETE") == Tier.ADMIN
    assert required_tier("/api/invites", "POST") == Tier.ADMIN
    # P2 security review: the support/diagnostics bundle bundles the FULL, unfiltered audit trail
    # (every "auth"-category row) + the server-log tail — ADMIN for every method, reads included,
    # via the exact-path ADMIN_PATHS set (it isn't under an _ADMIN_PREFIXES prefix). `/api/export`
    # (the plain single-table CSV/JSON download, a DIFFERENT endpoint) stays VIEW — only the
    # `/package` bundle is gated.
    assert required_tier("/api/export/package", "GET") == Tier.ADMIN
    assert required_tier("/api/export", "GET") == Tier.VIEW
    # /api/audit itself stays VIEW — reader/user roles legitimately use it for transparency into
    # decisions/config/overrides; the "auth"-category filtering is enforced in the handler, not
    # via the tier (see ems.web.api.audit_endpoint).
    assert required_tier("/api/audit", "GET") == Tier.VIEW


def test_requires_session():
    assert requires_session("/api/auth/password")
    assert requires_session("/api/auth/logout")
    assert requires_session("/api/auth/tokens")
    assert requires_session("/api/auth/tokens/3")
    assert not requires_session("/api/settings")


def test_tier_rank_and_role_rank():
    assert tier_rank("view") == 0 and tier_rank("operate") == 1 and tier_rank("admin") == 2
    assert tier_rank(None) == -1 and tier_rank("bogus") == -1
    assert role_rank("reader") == 0 and role_rank("user") == 1 and role_rank("admin") == 2
    assert role_rank("bogus") == -1


def test_effective_rank_session_is_owner_role():
    assert effective_rank("admin", "session", None) == int(Tier.ADMIN)
    assert effective_rank("user", "session", None) == int(Tier.OPERATE)


def test_effective_rank_access_is_min_of_owner_and_scope():
    # admin owner, read-only scope -> VIEW
    assert effective_rank("admin", "access", "view") == int(Tier.VIEW)
    # admin owner, operate scope -> OPERATE
    assert effective_rank("admin", "access", "operate") == int(Tier.OPERATE)
    # user owner, admin scope -> capped at owner (OPERATE)
    assert effective_rank("user", "access", "admin") == int(Tier.OPERATE)


def test_effective_rank_legacy_null_access_caps_at_operate():
    assert effective_rank("admin", "access", None) == int(Tier.OPERATE)
    assert effective_rank("user", "access", None) == int(Tier.OPERATE)


def test_effective_rank_fails_closed_on_garbage_tier():
    # Unknown non-null tier -> rank -1 -> below VIEW -> denies everything. Never KeyError.
    assert effective_rank("admin", "access", "root") == -1


def test_user_and_invite_management_is_session_only():
    assert requires_session("/api/users")
    assert requires_session("/api/users/5")
    assert requires_session("/api/invites")
    assert requires_session("/api/invites/9")
    # accept stays reachable unauthenticated — it's exempt, checked before requires_session.
    from ems.web.authz import EXEMPT_PATHS
    assert "/api/invites/accept" in EXEMPT_PATHS
