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


def test_user_crud_and_case_insensitive_unique(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        assert await s.user_count() == 0
        uid = await s.create_user("Alice", "hash1", "admin")
        assert await s.user_count() == 1
        u = await s.get_user_by_username("alice")  # COLLATE NOCASE
        assert u["id"] == uid and u["role"] == "admin"
        assert (await s.get_user_by_id(uid))["username"] == "Alice"
        dup = False
        try:
            await s.create_user("ALICE", "h", "user")
        except Exception:
            dup = True
        assert dup
        await s.set_password(uid, "hash2")
        assert (await s.get_user_by_username("alice"))["password_hash"] == "hash2"

    asyncio.run(run())
