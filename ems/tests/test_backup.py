"""Scheduled-backup + rotation (SPEC §11 durability, B-52). The maintenance loop's backup step is
extracted as `_run_backup(store, db_path, keep, state)` so it is testable without running the loop:
it must write one dated snapshot per day, skip an existing one, rotate to the newest `keep`, treat
keep<=0 as disabled, and swallow any failure (marking state) so it can never kill the loop.

B-20: a failure also pushes exactly one `backup_failed` notification per UTC calendar day (proving
the notification outbox end-to-end on its first real source) — see the `*_notifies_*` tests
below."""
import asyncio
from datetime import UTC, datetime, timedelta

from ems.notify import Notifier
from ems.storage.history import HistoryStore
from ems.web.api import _run_backup


def _fresh_state() -> dict:
    return {"last_backup_ts": None, "last_backup_ok": None,
            "last_backup_size": None, "backups_kept": 0}


def _make_store(tmp_path) -> HistoryStore:
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.init())
    return store


def test_run_backup_creates_dated_file_and_records_state(tmp_path):
    store = _make_store(tmp_path)
    state = _fresh_state()
    asyncio.run(_run_backup(store, store.db_path, 7, state))

    files = sorted(p.name for p in (tmp_path / "backups").glob("ems-*.sqlite"))
    assert len(files) == 1
    assert files[0] == f"ems-{datetime.now(UTC):%Y%m%d}.sqlite"
    assert state["last_backup_ok"] is True
    assert state["last_backup_ts"] is not None
    assert state["last_backup_size"] > 0
    assert state["backups_kept"] == 1


def test_run_backup_skips_when_todays_file_exists(tmp_path):
    # A second run the same day must NOT overwrite an existing snapshot (idempotent per day).
    store = _make_store(tmp_path)
    backups = tmp_path / "backups"
    backups.mkdir()
    today = backups / f"ems-{datetime.now(UTC):%Y%m%d}.sqlite"
    today.write_bytes(b"")  # pre-existing (empty) marker — proves backup_to isn't re-run

    state = _fresh_state()
    asyncio.run(_run_backup(store, store.db_path, 7, state))

    assert today.stat().st_size == 0  # untouched: backup_to was NOT called
    assert state["last_backup_ts"] is None  # no new backup taken this cycle
    assert state["backups_kept"] == 1  # prune still counted the existing file


def test_run_backup_rotates_to_newest_keep(tmp_path):
    # Older snapshots beyond `keep` are pruned; the newest (incl. today's) survive.
    store = _make_store(tmp_path)
    backups = tmp_path / "backups"
    backups.mkdir()
    for day in ("20260101", "20260102", "20260103", "20260104", "20260105"):
        (backups / f"ems-{day}.sqlite").write_bytes(b"seed")

    state = _fresh_state()
    asyncio.run(_run_backup(store, store.db_path, 3, state))  # +today's real snapshot, keep 3

    remaining = sorted(p.name for p in backups.glob("ems-*.sqlite"))
    assert len(remaining) == 3
    assert state["backups_kept"] == 3
    # The oldest seeds were deleted; today's snapshot (a July date) sorts last and survives.
    assert "ems-20260101.sqlite" not in remaining
    assert "ems-20260102.sqlite" not in remaining
    assert "ems-20260103.sqlite" not in remaining
    assert f"ems-{datetime.now(UTC):%Y%m%d}.sqlite" in remaining


def test_run_backup_keep_zero_disables(tmp_path):
    store = _make_store(tmp_path)
    state = _fresh_state()
    asyncio.run(_run_backup(store, store.db_path, 0, state))

    assert not (tmp_path / "backups").exists()  # nothing created
    assert state == _fresh_state()  # state untouched


def test_run_backup_failure_marks_not_ok_and_never_raises(tmp_path):
    # A store whose backup_to raises must leave the state marked failed and NEVER propagate — the
    # maintenance loop (retention + WAL truncate) must keep running.
    class _BoomStore:
        db_path = str(tmp_path / "ems.sqlite")

        async def backup_to(self, dest: str) -> int:
            raise OSError("disk full")

    state = _fresh_state()
    asyncio.run(_run_backup(_BoomStore(), _BoomStore.db_path, 7, state))  # must not raise

    assert state["last_backup_ok"] is False
    assert state["last_backup_ts"] is not None
    assert state["last_backup_size"] is None  # never got a size


class _FailingBackupStore(HistoryStore):
    """A real HistoryStore (so notifications actually persist) whose backup_to always fails —
    proves the B-20 outbox end-to-end on its first real source."""

    async def backup_to(self, dest_path: str) -> int:
        raise OSError("disk full")


def _notifications(store: HistoryStore) -> list[dict]:
    return asyncio.run(store.notifications_between(
        "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))


def test_run_backup_failure_sends_one_backup_failed_notification(tmp_path):
    store = _FailingBackupStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.init())
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    state = _fresh_state()

    asyncio.run(_run_backup(store, store.db_path, 7, state, notifier))

    rows = _notifications(store)
    assert len(rows) == 1
    assert rows[0]["key"] == "backup_failed"
    assert rows[0]["title"] == "Backup failed"
    assert state["last_backup_ok"] is False


def test_run_backup_repeated_same_day_failure_notifies_exactly_once(tmp_path):
    store = _FailingBackupStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.init())
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    state = _fresh_state()

    asyncio.run(_run_backup(store, store.db_path, 7, state, notifier))
    # Same UTC day, still failing.
    asyncio.run(_run_backup(store, store.db_path, 7, state, notifier))

    assert len(_notifications(store)) == 1  # deduped — same calendar day


def test_run_backup_failure_on_a_new_day_notifies_again(tmp_path):
    # Seed "yesterday's" already-sent notification directly, then let a real failure through
    # _run_backup for TODAY — a new day's dedupe_key must always get through.
    store = _FailingBackupStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.init())
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    asyncio.run(store.add_notification(
        f"{yesterday}T09:00:00+00:00", "backup_failed", "Backup failed", "yesterday's failure",
        dedupe_key=f"backup_failed:{yesterday}",
    ))
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    state = _fresh_state()

    asyncio.run(_run_backup(store, store.db_path, 7, state, notifier))

    assert len(_notifications(store)) == 2  # yesterday's seed + today's fresh notification


def test_run_backup_success_sends_no_notification(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.init())
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    state = _fresh_state()

    asyncio.run(_run_backup(store, store.db_path, 7, state, notifier))

    assert _notifications(store) == []
    assert state["last_backup_ok"] is True
