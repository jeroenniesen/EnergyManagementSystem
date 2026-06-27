import asyncio

from ems.storage.settings import SettingsStore


def test_set_and_read_roundtrip(tmp_path):
    store = SettingsStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.set_many({"planner.charge_slots": 8, "ui.theme": "dark"})
        return await store.all()

    out = asyncio.run(run())
    assert out == {"planner.charge_slots": 8, "ui.theme": "dark"}


def test_set_many_upserts(tmp_path):
    store = SettingsStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.set_many({"ui.theme": "dark"})
        await store.set_many({"ui.theme": "light"})  # overwrite, not duplicate
        return await store.all()

    out = asyncio.run(run())
    assert out == {"ui.theme": "light"}


def test_empty_set_many_is_noop(tmp_path):
    store = SettingsStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.set_many({})
        return await store.all()

    assert asyncio.run(run()) == {}
