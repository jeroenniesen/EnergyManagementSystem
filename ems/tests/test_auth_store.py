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


def test_token_create_resolve_revoke(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "user")
        raw = await s.create_token(uid, "session")
        p = await s.resolve(raw)
        assert p.user_id == uid and p.role == "user" and p.kind == "session"
        assert await s.resolve("nope") is None
        # owner-scoped: a different user id cannot revoke it
        assert await s.revoke_token(p.token_id, uid + 999) is False
        assert await s.resolve(raw) is not None
        assert await s.revoke_token(p.token_id, uid) is True
        assert await s.resolve(raw) is None

    asyncio.run(run())


def test_disabled_user_token_and_sliding_refresh(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "user")
        # a session already inside the 7-day refresh window
        from datetime import UTC, datetime, timedelta

        from ems.authn import hash_token, new_token
        raw = new_token()
        soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        async with s._write_conn() as db:
            await db.execute(
                "INSERT INTO auth_tokens (user_id, token_hash, kind, created_at, expires_at) "
                "VALUES (?,?, 'session', ?, ?)",
                (uid, hash_token(raw), datetime.now(UTC).isoformat(), soon),
            )
            await db.commit()
        assert await s.resolve(raw) is not None  # bumps expiry
        async with s._conn() as db:
            cur = await db.execute("SELECT expires_at FROM auth_tokens WHERE user_id=?", (uid,))
            new_exp = datetime.fromisoformat((await cur.fetchone())[0])
        assert new_exp > datetime.now(UTC) + timedelta(days=20)  # slid to ~30d
        # disabling the user rejects the token
        async with s._write_conn() as db:
            await db.execute("UPDATE users SET disabled=1 WHERE id=?", (uid,))
            await db.commit()
        assert await s.resolve(raw) is None

    asyncio.run(run())
