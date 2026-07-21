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


async def _init_and_close(store: AuthStore) -> None:
    await store.init()
    await store.close()


def _token_columns(db_path: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(auth_tokens)").fetchall()}
    finally:
        con.close()


def test_fresh_db_has_tier_column(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    s = AuthStore(db)
    asyncio.run(_init_and_close(s))
    assert "tier" in _token_columns(db)


def test_tier_column_migration_is_idempotent(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    # Simulate a pre-slice-5 DB: auth_tokens WITHOUT the tier column.
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE auth_tokens (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
        "token_hash TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, name TEXT, "
        "created_at TEXT NOT NULL, last_used_at TEXT, expires_at TEXT)"
    )
    con.commit()
    con.close()
    s = AuthStore(db)

    async def run():
        await s.init()  # adds tier
        await s.init()  # must not fail on the second pass
        await s.close()

    asyncio.run(run())
    assert "tier" in _token_columns(db)


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


def test_last_used_at_is_best_effort_throttled_not_written_every_resolve(tmp_path):
    # SPEC §4: last_used_at is best-effort telemetry, not written on every request. resolve() is the
    # per-request auth hot path; an unconditional write there serializes every authenticated read
    # into a SQLite commit (the e2e write-lock contention). First resolve records it (was NULL); a
    # second resolve inside the throttle window must leave it unchanged.
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "user")
        raw = await s.create_token(uid, "access", name="script")

        async def last_used() -> str | None:
            async with s._conn() as db:
                cur = await db.execute(
                    "SELECT last_used_at FROM auth_tokens WHERE user_id=?", (uid,)
                )
                return (await cur.fetchone())[0]

        assert await last_used() is None
        assert await s.resolve(raw) is not None
        first = await last_used()
        assert first is not None  # first use records it
        assert await s.resolve(raw) is not None
        assert await last_used() == first  # throttled: unchanged on a rapid second resolve

    asyncio.run(run())


# --- Slice 2: user management + invites ---------------------------------------------------------


def test_list_users_excludes_password_hash(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        await s.create_user("admin", "secret-hash", "admin")
        return await s.list_users()

    users = asyncio.run(run())
    assert len(users) == 1
    u = users[0]
    assert u["username"] == "admin" and u["role"] == "admin" and u["disabled"] == 0
    assert "password_hash" not in u
    assert set(u) == {"id", "username", "role", "disabled", "created_at", "last_login_at"}


def test_invite_roundtrip(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        admin_id = await s.create_user("admin", "h", "admin")
        raw = await s.create_invite("user", created_by=admin_id)
        invites = await s.list_invites()
        assert len(invites) == 1
        assert invites[0]["role"] == "user" and invites[0]["used_at"] is None
        assert "token_hash" not in invites[0]
        result = await s.accept_invite(raw, "newbie", "pw-hash")
        assert result is not None
        uid, session_raw = result
        u = await s.get_user_by_id(uid)
        assert u["username"] == "newbie" and u["role"] == "user"
        p = await s.resolve(session_raw)
        assert p is not None and p.user_id == uid and p.kind == "session"
        invites_after = await s.list_invites()
        assert invites_after[0]["used_at"] is not None
        # single-use: the same raw code cannot be accepted again
        assert await s.accept_invite(raw, "someone-else", "h2") is None

    asyncio.run(run())


def test_expired_invite_rejected(tmp_path):
    from datetime import UTC, datetime, timedelta

    from ems.authn import hash_token, new_token

    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        admin_id = await s.create_user("admin", "h", "admin")
        raw = new_token()
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        async with s._write_conn() as db:
            await db.execute(
                "INSERT INTO invites (token_hash, role, created_by, created_at, expires_at) "
                "VALUES (?,?,?,?,?)",
                (hash_token(raw), "user", admin_id, past, past),
            )
            await db.commit()
        return await s.accept_invite(raw, "someone", "h")

    assert asyncio.run(run()) is None


def test_garbage_invite_code_rejected(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        return await s.accept_invite("not-a-real-code", "someone", "h")

    assert asyncio.run(run()) is None


def test_invite_accept_username_collision_raises_and_invite_stays_usable(tmp_path):
    from ems.storage.auth import UsernameTaken

    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        admin_id = await s.create_user("admin", "h", "admin")
        await s.create_user("taken", "h2", "user")
        raw = await s.create_invite("user", created_by=admin_id)
        raised = False
        try:
            await s.accept_invite(raw, "taken", "h3")
        except UsernameTaken:
            raised = True
        assert raised
        # the invite was NOT burned — the same code still works with a fresh username
        result = await s.accept_invite(raw, "not-taken", "h3")
        assert result is not None

    asyncio.run(run())


def test_revoke_invite(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        admin_id = await s.create_user("admin", "h", "admin")
        raw = await s.create_invite("user", created_by=admin_id)
        invite_id = (await s.list_invites())[0]["id"]
        assert await s.revoke_invite(invite_id) is True
        assert await s.revoke_invite(invite_id) is False  # already gone
        assert await s.accept_invite(raw, "x", "h") is None

    asyncio.run(run())


def test_double_accept_consumed_exactly_once_under_concurrency(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        admin_id = await s.create_user("admin", "h", "admin")
        raw = await s.create_invite("user", created_by=admin_id)
        results = await asyncio.gather(
            s.accept_invite(raw, "racer-a", "ha"),
            s.accept_invite(raw, "racer-b", "hb"),
            return_exceptions=True,
        )
        return results, await s.user_count()

    results, count = asyncio.run(run())
    # exactly one admin at start + exactly one new user from the single-use invite
    assert count == 2
    successes = [r for r in results if isinstance(r, tuple)]
    assert len(successes) == 1
    # the loser is a clean None (invite already consumed), never a raised exception
    for r in results:
        assert isinstance(r, tuple) or r is None


def test_set_role_last_admin_guard_and_self_demote_refused(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        a1 = await s.create_user("a1", "h", "admin")
        u1 = await s.create_user("u1", "h", "user")
        # only admin can't demote themselves
        assert await s.set_role(a1, "user", actor_id=a1) is False
        assert (await s.get_user_by_id(a1))["role"] == "admin"  # unchanged
        # a second admin lets the first be demoted by someone else, but not the last one
        a2 = await s.create_user("a2", "h", "admin")
        assert await s.set_role(a1, "user", actor_id=a2) is True
        assert (await s.get_user_by_id(a1))["role"] == "user"
        # now a2 is the only admin left — demoting them (even by another actor) is blocked
        assert await s.set_role(a2, "user", actor_id=u1) is False
        assert (await s.get_user_by_id(a2))["role"] == "admin"
        # promotions are never guarded
        assert await s.set_role(u1, "admin", actor_id=a2) is True
        # unknown user
        assert await s.set_role(999999, "user", actor_id=a2) is False

    asyncio.run(run())


def test_set_role_parallel_demotes_leave_at_least_one_admin(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        a1 = await s.create_user("a1", "h", "admin")
        a2 = await s.create_user("a2", "h", "admin")
        # each demotes the OTHER concurrently, actor != target so self-demote guard doesn't apply
        await asyncio.gather(
            s.set_role(a1, "user", actor_id=a2),
            s.set_role(a2, "user", actor_id=a1),
            return_exceptions=True,
        )
        async with s._conn() as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND "
                                    "disabled=0")
            return (await cur.fetchone())["n"]

    remaining_admins = asyncio.run(run())
    assert remaining_admins >= 1


def test_set_disabled_last_admin_guard_self_disable_refused_and_revokes_tokens(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        a1 = await s.create_user("a1", "h", "admin")
        raw1 = await s.create_token(a1, "session")
        # cannot disable yourself
        assert await s.set_disabled(a1, True, actor_id=a1) is False
        assert await s.resolve(raw1) is not None
        # a second admin can disable the first — tokens revoked immediately
        a2 = await s.create_user("a2", "h", "admin")
        assert await s.set_disabled(a1, True, actor_id=a2) is True
        assert await s.resolve(raw1) is None  # session dead immediately, not just user disabled
        # now a2 is the last enabled admin — nobody else can disable them
        u1 = await s.create_user("u1", "h", "user")
        assert await s.set_disabled(a2, True, actor_id=u1) is False
        assert (await s.get_user_by_id(a2))["disabled"] == 0
        # re-enabling never needs the guard
        assert await s.set_disabled(a1, False, actor_id=a2) is True
        assert (await s.get_user_by_id(a1))["disabled"] == 0
        # unknown user
        assert await s.set_disabled(999999, True, actor_id=a2) is False

    asyncio.run(run())


# --- Slice 3: long-lived access tokens (mint/list/revoke + atomic replace) ----------------------


def test_replace_token_atomic_revoke_and_remint(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "user")
        raw1 = await s.create_token(uid, "access", name="widget")
        raw2 = await s.replace_token(uid, "widget")
        # the old raw is dead, the new one works, exactly one row named "widget" survives.
        p1 = await s.resolve(raw1)
        p2 = await s.resolve(raw2)
        rows = await s.list_tokens(uid)
        return uid, p1, p2, rows

    uid, p1, p2, rows = asyncio.run(run())
    assert p1 is None
    assert p2 is not None and p2.user_id == uid and p2.kind == "access"
    named_widget = [r for r in rows if r["name"] == "widget"]
    assert len(named_widget) == 1


def test_replace_token_leaves_other_names_and_other_users_untouched(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "user")
        other = await s.create_user("b", "h", "user")
        raw_other_name = await s.create_token(uid, "access", name="script")
        raw_other_user = await s.create_token(other, "access", name="widget")
        await s.replace_token(uid, "widget")
        # a differently-named token for the SAME user is untouched.
        p_other_name = await s.resolve(raw_other_name)
        # a same-named token for a DIFFERENT user is untouched (replace is scoped to user_id AND
        # name).
        p_other_user = await s.resolve(raw_other_user)
        return p_other_name, p_other_user

    p_other_name, p_other_user = asyncio.run(run())
    assert p_other_name is not None
    assert p_other_user is not None


def test_replace_token_concurrent_yields_exactly_one_survivor(tmp_path):
    # Two callers race `replace_token(uid, "widget")` for the SAME (user, name). Semantics
    # implemented (see AuthStore.replace_token's docstring): "last commit wins" — each caller gets
    # its own raw back, but only whichever transaction commits LAST resolves; the loser's raw is
    # silently dead. Deterministic invariant asserted here (order-independent): after both
    # complete, EXACTLY ONE of the two raws resolves, and exactly one "widget" row exists.
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        uid = await s.create_user("a", "h", "user")
        raws = await asyncio.gather(
            s.replace_token(uid, "widget"), s.replace_token(uid, "widget"),
        )
        results = [await s.resolve(r) for r in raws]
        rows = await s.list_tokens(uid)
        return results, rows

    results, rows = asyncio.run(run())
    resolved = [r for r in results if r is not None]
    assert len(resolved) == 1  # exactly one of the two raws is valid
    named_widget = [r for r in rows if r["name"] == "widget"]
    assert len(named_widget) == 1  # exactly one row with that name


def test_set_disabled_parallel_last_admin_attempts_leave_at_least_one_admin(tmp_path):
    s = AuthStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await s.init()
        a1 = await s.create_user("a1", "h", "admin")
        a2 = await s.create_user("a2", "h", "admin")
        await asyncio.gather(
            s.set_disabled(a1, True, actor_id=a2),
            s.set_disabled(a2, True, actor_id=a1),
            return_exceptions=True,
        )
        async with s._conn() as db:
            import aiosqlite
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND "
                                    "disabled=0")
            return (await cur.fetchone())["n"]

    remaining_admins = asyncio.run(run())
    assert remaining_admins >= 1
