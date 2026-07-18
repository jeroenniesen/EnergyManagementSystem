import asyncio
import sqlite3

from ems.storage.auth import AuthStore


def _tables(db_path: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def test_init_creates_tables_idempotently(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    store = AuthStore(db)

    async def run():
        await store.init()
        await store.init()  # must be idempotent
        await store.close()

    asyncio.run(run())
    assert {"users", "auth_tokens", "invites"} <= _tables(db)
