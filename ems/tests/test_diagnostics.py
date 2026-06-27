import pytest

from ems.diagnostics import Check, build_diagnostics, overall_status


def test_check_rejects_invalid_status():
    # A typo'd status must fail loudly at construction, not silently rank as ok.
    with pytest.raises(ValueError):
        Check("x", "X", "greenish", "detail")


def _facts(**over):
    base = dict(
        dev_mode="mock", dry_run=True, data_quality="complete",
        prices_ok=True, forecast_ok=True, battery_ok=True, p1_paired=True,
        plan_ok=True, store_ok=True, settings_store_ok=True, auth_required=False,
    )
    base.update(over)
    return base


def test_all_healthy_is_overall_ok():
    checks = build_diagnostics(**_facts())
    assert overall_status(checks) == "ok"
    assert {c.key for c in checks} >= {"history_store", "prices", "battery", "data_quality", "auth"}


def test_unreachable_history_store_fails_overall():
    checks = build_diagnostics(**_facts(store_ok=False))
    store = next(c for c in checks if c.key == "history_store")
    assert store.status == "fail"
    assert overall_status(checks) == "fail"


def test_missing_prices_is_a_warning_not_a_failure():
    checks = build_diagnostics(**_facts(prices_ok=False, plan_ok=False))
    assert next(c for c in checks if c.key == "prices").status == "warn"
    assert overall_status(checks) == "warn"


def test_unsafe_data_quality_fails():
    checks = build_diagnostics(**_facts(data_quality="unsafe"))
    assert next(c for c in checks if c.key == "data_quality").status == "fail"
    assert overall_status(checks) == "fail"


def test_auth_check_reflects_protection():
    open_checks = build_diagnostics(**_facts(auth_required=False))
    assert "open" in next(c for c in open_checks if c.key == "auth").detail
    prot = build_diagnostics(**_facts(auth_required=True))
    assert "protected" in next(c for c in prot if c.key == "auth").detail


def test_overall_status_empty_is_ok():
    assert overall_status([]) == "ok"
