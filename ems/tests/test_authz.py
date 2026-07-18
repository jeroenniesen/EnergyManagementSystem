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
    assert required_tier("/api/users", "GET") == Tier.ADMIN
    assert required_tier("/api/users/5", "DELETE") == Tier.ADMIN
    assert required_tier("/api/invites", "POST") == Tier.ADMIN


def test_requires_session():
    assert requires_session("/api/auth/password")
    assert requires_session("/api/auth/logout")
    assert requires_session("/api/auth/tokens")
    assert requires_session("/api/auth/tokens/3")
    assert not requires_session("/api/settings")
