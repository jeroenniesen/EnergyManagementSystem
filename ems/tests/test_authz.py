from ems.web.authz import Tier, required_tier, requires_session, role_satisfies


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
