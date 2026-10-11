"""#119 — neutral `grid_fees.price_includes_all_fees` + legacy Tibber-named alias."""
from __future__ import annotations

from ems.settings import (
    SETTINGS_BY_KEY,
    effective_settings,
    settings_migration_writes,
    validate_settings,
)
from ems.tariffs import (
    LEGACY_PRICE_INCLUDES_ALL_FEES_KEY,
    PRICE_INCLUDES_ALL_FEES_KEY,
    TariffPolicy,
    policy_from_settings,
    policy_to_dict,
    setting_price_includes_all_fees,
)


def test_schema_uses_neutral_key_not_tibber_name():
    assert PRICE_INCLUDES_ALL_FEES_KEY in SETTINGS_BY_KEY
    assert LEGACY_PRICE_INCLUDES_ALL_FEES_KEY not in SETTINGS_BY_KEY


def test_validate_accepts_legacy_alias_and_writes_canonical():
    clean, errors = validate_settings({LEGACY_PRICE_INCLUDES_ALL_FEES_KEY: True})
    assert errors == {}
    assert clean == {PRICE_INCLUDES_ALL_FEES_KEY: True}


def test_canonical_wins_when_both_keys_submitted():
    clean, errors = validate_settings({
        PRICE_INCLUDES_ALL_FEES_KEY: False,
        LEGACY_PRICE_INCLUDES_ALL_FEES_KEY: True,
    })
    assert errors == {}
    assert clean[PRICE_INCLUDES_ALL_FEES_KEY] is False


def test_effective_settings_migrates_legacy_value():
    eff = effective_settings({LEGACY_PRICE_INCLUDES_ALL_FEES_KEY: True})
    assert eff[PRICE_INCLUDES_ALL_FEES_KEY] is True


def test_migration_writes_persist_canonical_once():
    stored = {LEGACY_PRICE_INCLUDES_ALL_FEES_KEY: True}
    assert settings_migration_writes(stored) == {PRICE_INCLUDES_ALL_FEES_KEY: True}
    # Already migrated — nothing to write.
    assert settings_migration_writes({
        PRICE_INCLUDES_ALL_FEES_KEY: True,
        LEGACY_PRICE_INCLUDES_ALL_FEES_KEY: False,
    }) == {}


def test_policy_from_settings_reads_legacy_and_canonical():
    assert policy_from_settings(
        {LEGACY_PRICE_INCLUDES_ALL_FEES_KEY: True}
    ).price_includes_all_fees is True
    assert policy_from_settings(
        {PRICE_INCLUDES_ALL_FEES_KEY: True}
    ).price_includes_all_fees is True
    # Canonical wins over a conflicting legacy row.
    assert setting_price_includes_all_fees({
        PRICE_INCLUDES_ALL_FEES_KEY: False,
        LEGACY_PRICE_INCLUDES_ALL_FEES_KEY: True,
    }) is False


def test_policy_to_dict_uses_neutral_name():
    d = policy_to_dict(TariffPolicy(price_includes_all_fees=True))
    assert d["price_includes_all_fees"] is True
    assert "tibber_total_includes_all" not in d


def test_finance_kwargs_rename_keeps_same_import_boundary():
    """Reporting outcomes stay equal after the rename (same fee suppression)."""
    from ems.finance import day_finance

    # Empty inputs → identical empty DayFinance either way.
    a = day_finance([], [], day="2026-07-11", price_includes_all_fees=True)
    b = day_finance([], [], day="2026-07-11", price_includes_all_fees=False)
    assert a.to_dict() == b.to_dict()


def test_boot_persists_migrated_canonical_key(tmp_path):
    """Startup writes the neutral key when only the legacy alias is stored (#119)."""
    import asyncio

    from fastapi.testclient import TestClient

    from ems.sources.mock import MockSource
    from ems.storage.history import HistoryStore
    from ems.storage.settings import SettingsStore
    from ems.web.api import create_app

    db = str(tmp_path / "ems.sqlite")

    async def seed():
        store = SettingsStore(db)
        await store.init()
        await store.set_many({LEGACY_PRICE_INCLUDES_ALL_FEES_KEY: True})
        await store.close()

    asyncio.run(seed())

    app = create_app(
        MockSource(),
        dry_run=True,
        config_dry_run=False,
        dev_mode="mock",
        store=HistoryStore(db),
        settings_store=SettingsStore(db),
    )
    with TestClient(app) as c:
        body = c.get("/api/settings").json()
    assert body["values"][PRICE_INCLUDES_ALL_FEES_KEY] is True

    async def read_back():
        store = SettingsStore(db)
        await store.init()
        all_rows = await store.all()
        await store.close()
        return all_rows

    stored = asyncio.run(read_back())
    assert stored.get(PRICE_INCLUDES_ALL_FEES_KEY) is True
