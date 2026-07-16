"""Exact-provenance prediction ledger (design §4.2 / §4.3): migration v3 (legacy snapshot backfill
with the documented date-only approximation, PK dedupe, index), the store helpers' first-write-wins,
the recorder's throttled nowcast append, the 18:00 canonical day-ahead job (gate, dedupe, full
DST-aware slot coverage, retry-until-20:00-then-exclude, baseline load bands), the 400-day purge,
and proof the legacy forecast_snapshots WRITE path is retired (the recorder no longer writes it;
only the ledger is written — see §3.3's reconciliation)."""
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite

from ems.freshness import FreshnessTracker
from ems.notify import Notifier
from ems.planner.load_profile import LoadProfile
from ems.sense import SIGNALS, Recorder
from ems.sources.forecast import ForecastSlot, MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.storage.cache import CacheStore
from ems.storage.history import HistoryStore
from ems.web.api import _canonical_ledger_rows, _run_canonical_forecast

_AMS = ZoneInfo("Europe/Amsterdam")
_WIDE = ("0000", "9999")  # a target-window that spans every stored slot


class _StubForecast:
    """Minimal solar forecast source: .slots() → objects with .start/.p10_w/.p50_w/.p90_w."""

    def __init__(self, slots):
        self._slots = slots

    def slots(self):
        return self._slots


# --- Migration v3 -------------------------------------------------------------------------------

async def _make_v0_with_snapshots(path: str, snaps: list[tuple]) -> None:
    """A pre-runner v0 DB (user_version=0) with the raw/derived roots + a legacy forecast_snapshots
    table carrying `snaps` rows (issued_date, start, p10, p50, p90)."""
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            "CREATE TABLE raw_samples "
            "(ts TEXT NOT NULL, grid_power_w REAL NOT NULL, solar_power_w REAL NOT NULL, "
            "battery_power_w REAL NOT NULL, ev_power_w REAL NOT NULL, soc_pct REAL NOT NULL)")
        await db.execute(
            "CREATE TABLE derived_samples "
            "(ts TEXT NOT NULL, house_load_w REAL NOT NULL, non_ev_load_w REAL NOT NULL)")
        await db.execute(
            "CREATE TABLE forecast_snapshots "
            "(issued_date TEXT NOT NULL, start TEXT NOT NULL, p10_w REAL NOT NULL, "
            "p50_w REAL NOT NULL, p90_w REAL NOT NULL, PRIMARY KEY (issued_date, start))")
        for s in snaps:
            await db.execute("INSERT INTO forecast_snapshots VALUES (?,?,?,?,?)", s)
        await db.commit()


def test_v3_migrates_legacy_snapshots_with_documented_approximation(tmp_path):
    # Legacy date-keyed snapshots copy into the ledger as canonical solar rows: issued_at is the
    # DOCUMENTED date-only approximation (issued_date + T00:00:00+00:00), values map p10/p50/p90 →
    # low/expected/high, source='legacy_snapshot', canonical=1. The index also exists.
    path = str(tmp_path / "ems.sqlite")

    async def run():
        await _make_v0_with_snapshots(path, [
            ("2026-07-10", "2026-07-10T10:00:00+00:00", 100.0, 200.0, 300.0),
            ("2026-07-10", "2026-07-10T10:15:00+00:00", 110.0, 210.0, 310.0),
            ("2026-07-11", "2026-07-11T10:00:00+00:00", 120.0, 220.0, 320.0),
        ])
        store = HistoryStore(path)
        await store.init()
        rows = await store.ledger_canonical_between("solar", *_WIDE)
        async with aiosqlite.connect(path) as db:
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                ("idx_forecast_ledger_canonical",))
            has_index = await cur.fetchone() is not None
        return await store.schema_version(), rows, has_index

    version, rows, has_index = asyncio.run(run())
    assert version == 3
    assert has_index
    assert len(rows) == 3
    first = rows[0]
    assert first["issued_at"] == "2026-07-10T00:00:00+00:00"  # date-only ⇒ midnight approximation
    assert first["kind"] == "solar"
    assert first["target_start"] == "2026-07-10T10:00:00+00:00"
    assert (first["low_w"], first["expected_w"], first["high_w"]) == (100.0, 200.0, 300.0)
    assert first["source"] == "legacy_snapshot"
    assert first["canonical"] == 1
    assert first["quality"] is None and first["model_version"] is None


def test_v3_migration_pk_dedupes_and_is_idempotent(tmp_path):
    # A second init() (restart) must not duplicate migrated rows — the (issued_at, kind,
    # target_start) PK + INSERT OR IGNORE keep it a no-op.
    path = str(tmp_path / "ems.sqlite")

    async def run():
        await _make_v0_with_snapshots(path, [
            ("2026-07-10", "2026-07-10T10:00:00+00:00", 100.0, 200.0, 300.0)])
        store = HistoryStore(path)
        await store.init()
        one = await store.ledger_canonical_between("solar", *_WIDE)
        await store.init()  # second boot
        two = await store.ledger_canonical_between("solar", *_WIDE)
        return one, two

    one, two = asyncio.run(run())
    assert len(one) == 1 and one == two


def test_v3_without_legacy_table_still_creates_empty_ledger(tmp_path):
    # A fresh DB never runs the numbered migration; the ledger comes from the baseline and is empty.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        return await store.table_names(), await store.ledger_between("solar", *_WIDE)

    names, rows = asyncio.run(run())
    assert "forecast_ledger" in names and rows == []


# --- Store helpers ------------------------------------------------------------------------------

def test_ledger_append_first_write_wins(tmp_path):
    # INSERT OR IGNORE: the FIRST row for a (issued_at, kind, target_start) sticks; a later write
    # with the same key (a nowcast that shared an instant) never overwrites the provenance.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        key = ("2026-07-10T12:00:00+00:00", "solar", "2026-07-10T18:00:00+00:00")
        await store.ledger_append([(*key, 10.0, 20.0, 30.0, "srcA", None, None, 0)])
        await store.ledger_append([(*key, 999.0, 999.0, 999.0, "srcB", "v9", "High", 1)])
        return await store.ledger_between("solar", *_WIDE)

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert (rows[0]["low_w"], rows[0]["expected_w"], rows[0]["high_w"]) == (10.0, 20.0, 30.0)
    assert rows[0]["source"] == "srcA" and rows[0]["canonical"] == 0


def test_ledger_canonical_between_filters_canonical_and_kind(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.ledger_append([
            ("2026-07-10T00:00:00+00:00", "solar", "2026-07-10T10:00:00+00:00",
             1.0, 2.0, 3.0, "s", None, None, 1),
            ("2026-07-10T12:00:00+00:00", "solar", "2026-07-10T10:00:00+00:00",
             4.0, 5.0, 6.0, "s", None, None, 0),  # nowcast, not canonical
            ("2026-07-10T00:00:00+00:00", "load", "2026-07-10T10:00:00+00:00",
             7.0, 8.0, 9.0, "b", None, None, 1),
        ])
        solar = await store.ledger_canonical_between("solar", *_WIDE)
        allsolar = await store.ledger_between("solar", *_WIDE)
        return solar, allsolar

    solar, allsolar = asyncio.run(run())
    assert len(solar) == 1 and solar[0]["canonical"] == 1  # nowcast + load excluded
    assert len(allsolar) == 2  # ledger_between keeps every issue time


# --- Recorder throttle --------------------------------------------------------------------------

def test_recorder_ledger_write_is_throttled_to_30_min(tmp_path):
    # Two sense cycles 5 min apart append the ledger only ONCE (the throttle); a third at +35 min
    # crosses the interval and appends a second issue time. The legacy snapshot is unaffected.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    t0 = datetime(2026, 7, 10, 10, 0, tzinfo=UTC)
    slots = [ForecastSlot(t0, 100.0, 200.0, 300.0)]
    rec = Recorder(MockSource(), store, fresh, solar_forecast=_StubForecast(slots))

    async def run():
        await store.init()
        await rec.sense_once(t0)
        await rec.sense_once(t0 + timedelta(minutes=5))
        after_two = await store.ledger_between("solar", *_WIDE)
        await rec.sense_once(t0 + timedelta(minutes=35))
        after_three = await store.ledger_between("solar", *_WIDE)
        return after_two, after_three

    two, three = asyncio.run(run())
    assert {r["issued_at"] for r in two} == {t0.isoformat()}  # one issue time despite two cycles
    assert len(two) == 1
    issued = {r["issued_at"] for r in three}
    assert issued == {t0.isoformat(), (t0 + timedelta(minutes=35)).isoformat()}  # +35 crossed


def test_recorder_writes_ledger_only_snapshot_table_no_longer_written(tmp_path):
    # Reconciliation iteration (design §3.3): the legacy forecast_snapshots write is RETIRED — the
    # recorder appends solar forecasts to the prediction ledger EXCLUSIVELY now. This is the proof
    # that the legacy table is no longer written by anything while the ledger still is (every
    # solar-accuracy surface scores the ledger's canonical rows, so a stale-but-still-fed legacy
    # table would risk a second, contradictory read).
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    t0 = datetime(2026, 7, 10, 10, 0, tzinfo=UTC)
    slots = [ForecastSlot(t0, 100.0, 200.0, 300.0)]
    rec = Recorder(MockSource(), store, fresh, solar_forecast=_StubForecast(slots))

    async def run():
        await store.init()
        await rec.sense_once(t0)
        snaps = await store.forecasts_between("2020-01-01T00:00:00+00:00",
                                              "2030-01-01T00:00:00+00:00")
        ledger = await store.ledger_between("solar", *_WIDE)
        return snaps, ledger

    snaps, ledger = asyncio.run(run())
    assert snaps == []  # legacy forecast_snapshots table is NEVER written by the recorder anymore
    assert len(ledger) == 1
    assert ledger[0]["issued_at"] == t0.isoformat()  # ledger carries the TRUE issue time
    assert ledger[0]["source"] == "_StubForecast" and ledger[0]["canonical"] == 0


# --- Canonical 18:00 job ------------------------------------------------------------------------

def _flat_profile(watts: float) -> LoadProfile:
    return LoadProfile(by_hour={h: watts for h in range(24)}, tz=_AMS)


def _mock_solar(now: datetime) -> MockSolarForecastSource:
    return MockSolarForecastSource(_AMS, clock=lambda: now)


def _tomorrow_bounds(now_local: datetime) -> tuple[str, str]:
    tomorrow = now_local.date() + timedelta(days=1)
    start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=_AMS)
    end = start + timedelta(days=1)
    return start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()


def test_canonical_fires_after_18_covers_96_slots_with_baseline_load(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    cache = CacheStore(str(tmp_path / "ems.sqlite"))
    now_local = datetime(2026, 7, 10, 18, 30, tzinfo=_AMS)  # a normal (96-slot) day
    lo, hi = _tomorrow_bounds(now_local)

    async def run():
        await store.init()
        await asyncio.to_thread(cache.init)
        n = await _run_canonical_forecast(
            store, cache, now_local, _AMS,
            solar_slots=_mock_solar(now_local).slots(), solar_source_name="MockSolar",
            load_profile=_flat_profile(500.0))
        load = await store.ledger_canonical_between("load", lo, hi)
        solar = await store.ledger_canonical_between("solar", lo, hi)
        return n, load, solar

    n, load, solar = asyncio.run(run())
    assert len(load) == 96 and len(solar) == 96  # every 15-min slot of tomorrow, both kinds
    assert n == len(load) + len(solar)
    assert all(r["canonical"] == 1 for r in load + solar)
    # Load rows carry the learned baseline: expected = profile mean, bands = ±30% (clamped ≥ 0).
    assert all(r["source"] == "baseline_profile" for r in load)
    assert load[0]["expected_w"] == 500.0
    assert load[0]["low_w"] == 350.0 and load[0]["high_w"] == 650.0
    assert solar[0]["source"] == "MockSolar"


def test_canonical_dedupes_once_per_day(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    cache = CacheStore(str(tmp_path / "ems.sqlite"))
    now_local = datetime(2026, 7, 10, 18, 30, tzinfo=_AMS)
    lo, hi = _tomorrow_bounds(now_local)

    async def run():
        await store.init()
        await asyncio.to_thread(cache.init)
        first = await _run_canonical_forecast(
            store, cache, now_local, _AMS, solar_slots=_mock_solar(now_local).slots(),
            solar_source_name="MockSolar", load_profile=_flat_profile(500.0))
        # A later cycle the same evening must be a no-op (dedupe key set).
        second = await _run_canonical_forecast(
            store, cache, now_local + timedelta(minutes=20), _AMS,
            solar_slots=_mock_solar(now_local).slots(), solar_source_name="MockSolar",
            load_profile=_flat_profile(999.0))
        load = await store.ledger_canonical_between("load", lo, hi)
        return first, second, load

    first, second, load = asyncio.run(run())
    assert first is not None and second is None  # second didn't fire
    assert load[0]["expected_w"] == 500.0  # the 999 profile never got written


class _CacheSetBoom(CacheStore):
    """A cache whose `.set` fails (get still works) — simulates the dedupe key being lost AFTER a
    successful ledger write, the exact window in which a naive retry would duplicate (F3)."""

    def set(self, *a, **kw):
        raise RuntimeError("cache write failed")


def test_canonical_retry_after_cache_set_failure_writes_nothing_new(tmp_path):
    # F3: the first run writes the canonical set but the dedupe-key .set FAILS, so the cache never
    # learns tomorrow is done. Without a ledger-level guard, a later cycle the same evening would
    # write a SECOND canonical set with a fresh issued_at (duplicates the scorer double-counts).
    # The fix: before building, the job checks the ledger for tomorrow's canonical solar rows and,
    # finding them, treats the run as already-done — skipping the write.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    cache = _CacheSetBoom(str(tmp_path / "ems.sqlite"))
    now_local = datetime(2026, 7, 10, 18, 30, tzinfo=_AMS)
    lo, hi = _tomorrow_bounds(now_local)

    async def run():
        await store.init()
        await asyncio.to_thread(cache.init)
        first = await _run_canonical_forecast(
            store, cache, now_local, _AMS, solar_slots=_mock_solar(now_local).slots(),
            solar_source_name="MockSolar", load_profile=_flat_profile(500.0))
        canonical_after_first = await store.ledger_canonical_between("solar", lo, hi)
        # A later cycle the SAME evening. Cache still misses (its .set kept failing), so only the
        # ledger guard can stop the duplicate write.
        second = await _run_canonical_forecast(
            store, cache, now_local + timedelta(minutes=20), _AMS,
            solar_slots=_mock_solar(now_local).slots(), solar_source_name="MockSolar",
            load_profile=_flat_profile(999.0))
        canonical_after_second = await store.ledger_canonical_between("solar", lo, hi)
        all_solar = await store.ledger_between("solar", lo, hi)  # every issued_at
        return first, second, canonical_after_first, canonical_after_second, all_solar

    first, second, after_first, after_second, all_solar = asyncio.run(run())
    assert first is not None and first > 0  # first run wrote the canonical set
    assert second is None  # retry skipped — the ledger guard treated tomorrow as already done
    assert len(after_first) == len(after_second)  # no new canonical solar rows
    counts = Counter(r["target_start"] for r in all_solar)
    assert counts and all(c == 1 for c in counts.values())  # one canonical solar row per slot


def test_canonical_gate_closed_before_18_and_after_20(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    cache = CacheStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await asyncio.to_thread(cache.init)
        early = await _run_canonical_forecast(
            store, cache, datetime(2026, 7, 10, 17, 0, tzinfo=_AMS), _AMS,
            solar_slots=[], solar_source_name="MockSolar", load_profile=_flat_profile(500.0))
        late = await _run_canonical_forecast(
            store, cache, datetime(2026, 7, 10, 20, 30, tzinfo=_AMS), _AMS,
            solar_slots=[], solar_source_name="MockSolar", load_profile=_flat_profile(500.0))
        rows = await store.ledger_canonical_between("load", *_WIDE)
        return early, late, rows

    early, late, rows = asyncio.run(run())
    assert early is None and late is None  # before 18:00 and after 20:00 the window is closed
    assert rows == []  # nothing written


def test_canonical_dst_fall_back_is_100_slots(tmp_path):
    # 2026-10-25 is the 25-hour fall-back day: the DST-aware UTC grid emits 100 load slots.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    now_local = datetime(2026, 10, 24, 18, 30, tzinfo=_AMS)
    lo, hi = _tomorrow_bounds(now_local)

    async def run():
        await store.init()
        await _run_canonical_forecast(
            store, None, now_local, _AMS, solar_slots=[], solar_source_name="x",
            load_profile=_flat_profile(500.0))
        return await store.ledger_canonical_between("load", lo, hi)

    load = asyncio.run(run())
    assert len(load) == 100


def test_canonical_dst_spring_forward_is_92_slots(tmp_path):
    # 2026-03-29 is the 23-hour spring-forward day: the grid emits 92 load slots.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    now_local = datetime(2026, 3, 28, 18, 30, tzinfo=_AMS)
    lo, hi = _tomorrow_bounds(now_local)

    async def run():
        await store.init()
        await _run_canonical_forecast(
            store, None, now_local, _AMS, solar_slots=[], solar_source_name="x",
            load_profile=_flat_profile(500.0))
        return await store.ledger_canonical_between("load", lo, hi)

    load = asyncio.run(run())
    assert len(load) == 92


class _LedgerBoomStore(HistoryStore):
    """A store whose ledger_append fails, to prove the canonical write retries (no dedupe set)."""

    async def ledger_append(self, rows):
        raise RuntimeError("disk full")


def test_canonical_write_failure_retries_then_excluded_after_20(tmp_path):
    # An 18:xx write failure returns None AND leaves the dedupe key unset, so the next in-window
    # cycle retries. After 20:00 with still no success, the day is excluded (gate closed) — never
    # backfilled with hindsight (design §4.3).
    store = _LedgerBoomStore(str(tmp_path / "ems.sqlite"))
    good = HistoryStore(str(tmp_path / "ems.sqlite"))  # same file, working ledger_append
    cache = CacheStore(str(tmp_path / "ems.sqlite"))
    now_local = datetime(2026, 7, 10, 18, 30, tzinfo=_AMS)

    async def run():
        await store.init()
        await asyncio.to_thread(cache.init)
        fail = await _run_canonical_forecast(
            store, cache, now_local, _AMS, solar_slots=[], solar_source_name="x",
            load_profile=_flat_profile(500.0))
        dedupe_after_fail = await asyncio.to_thread(cache.get, "ledger:canonical:2026-07-11")
        # 19:30 — still in-window, retry succeeds on a healthy store.
        retry = await _run_canonical_forecast(
            good, cache, datetime(2026, 7, 10, 19, 30, tzinfo=_AMS), _AMS,
            solar_slots=[], solar_source_name="x", load_profile=_flat_profile(500.0))
        return fail, dedupe_after_fail, retry

    fail, dedupe_after_fail, retry = asyncio.run(run())
    assert fail is None  # write raised → treated as a fail-safe no-op
    assert dedupe_after_fail is None  # NOT deduped → next cycle retries
    assert retry == 92 or retry == 96 or retry == 100  # retry within the window succeeded


# --- Canonical job observability: state box + missed-day notification (mirrors _run_backup) -----

def _fresh_canonical_state() -> dict:
    return {"last_success_date": None, "last_attempt_iso": None, "ok": None}


def test_canonical_forecast_state_records_success(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    cache = CacheStore(str(tmp_path / "ems.sqlite"))
    now_local = datetime(2026, 7, 10, 18, 30, tzinfo=_AMS)
    state = _fresh_canonical_state()

    async def run():
        await store.init()
        await asyncio.to_thread(cache.init)
        await _run_canonical_forecast(
            store, cache, now_local, _AMS, solar_slots=_mock_solar(now_local).slots(),
            solar_source_name="MockSolar", load_profile=_flat_profile(500.0), state=state)
    asyncio.run(run())

    assert state["ok"] is True
    assert state["last_success_date"] == "2026-07-11"
    assert state["last_attempt_iso"] == now_local.isoformat()


def test_canonical_forecast_state_retry_then_success(tmp_path):
    # Mirrors test_canonical_write_failure_retries_then_excluded_after_20, but asserting on the
    # OBSERVABILITY state box instead of the ledger: a failed attempt marks ok=False without
    # touching last_success_date; a later in-window retry that succeeds flips both.
    boom = _LedgerBoomStore(str(tmp_path / "ems.sqlite"))
    good = HistoryStore(str(tmp_path / "ems.sqlite"))  # same file, working ledger_append
    cache = CacheStore(str(tmp_path / "ems.sqlite"))
    now_local = datetime(2026, 7, 10, 18, 30, tzinfo=_AMS)
    state = _fresh_canonical_state()

    async def run():
        await boom.init()
        await asyncio.to_thread(cache.init)
        await _run_canonical_forecast(
            boom, cache, now_local, _AMS, solar_slots=[], solar_source_name="x",
            load_profile=_flat_profile(500.0), state=state)
        after_fail = dict(state)
        await _run_canonical_forecast(
            good, cache, now_local + timedelta(minutes=30), _AMS, solar_slots=[],
            solar_source_name="x", load_profile=_flat_profile(500.0), state=state)
        return after_fail

    after_fail = asyncio.run(run())
    assert after_fail == {"last_success_date": None, "last_attempt_iso": now_local.isoformat(),
                          "ok": False}
    assert state["ok"] is True
    assert state["last_success_date"] == "2026-07-11"


def test_canonical_forecast_missed_day_sends_notification_once(tmp_path):
    # The window closing (>= 20:00) with no success recorded for tomorrow marks the state box
    # ok=False and sends exactly one `canonical_missed` notification for that day, even across
    # repeated cycles the same evening (Notifier's own dedupe_key, mirroring backup_failed).
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    state = _fresh_canonical_state()
    after_20 = datetime(2026, 7, 10, 20, 30, tzinfo=_AMS)

    async def run():
        await store.init()
        await _run_canonical_forecast(
            store, None, after_20, _AMS, solar_slots=[], solar_source_name="x",
            load_profile=None, state=state, notifier=notifier)
        await _run_canonical_forecast(
            store, None, after_20 + timedelta(minutes=15), _AMS, solar_slots=[],
            solar_source_name="x", load_profile=None, state=state, notifier=notifier)
        return await store.notifications_between(
            "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")

    rows = asyncio.run(run())
    assert state["ok"] is False
    assert len(rows) == 1
    assert rows[0]["key"] == "canonical_missed"


def test_canonical_forecast_success_before_20_suppresses_missed_notification(tmp_path):
    # A day that DID get its canonical snapshot before 20:00 must not be flagged missed later the
    # same evening, and no notification is sent.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    cache = CacheStore(str(tmp_path / "ems.sqlite"))
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    state = _fresh_canonical_state()
    now_local = datetime(2026, 7, 10, 18, 30, tzinfo=_AMS)

    async def run():
        await store.init()
        await asyncio.to_thread(cache.init)
        await _run_canonical_forecast(
            store, cache, now_local, _AMS, solar_slots=_mock_solar(now_local).slots(),
            solar_source_name="MockSolar", load_profile=_flat_profile(500.0),
            state=state, notifier=notifier)
        await _run_canonical_forecast(
            store, cache, datetime(2026, 7, 10, 20, 30, tzinfo=_AMS), _AMS, solar_slots=[],
            solar_source_name="x", load_profile=None, state=state, notifier=notifier)
        return await store.notifications_between(
            "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")

    rows = asyncio.run(run())
    assert state["ok"] is True
    assert rows == []


def test_canonical_ledger_rows_builder_is_pure_dst_aware():
    # The pure builder generates the DST-correct UTC grid and pairs LOAD (always) with SOLAR
    # (only where the forecast has that exact slot).
    from datetime import date as date_cls
    tomorrow = date_cls(2026, 10, 25)  # 100-slot fall-back day
    # A single solar slot at 12:00 local (= 11:00 UTC on that day) so exactly one solar row appears.
    noon_utc = datetime(2026, 10, 25, 12, 0, tzinfo=_AMS).astimezone(UTC)
    solar = [ForecastSlot(noon_utc, 50.0, 100.0, 150.0)]
    rows = _canonical_ledger_rows(
        issued_at="2026-10-24T16:30:00+00:00", tomorrow=tomorrow, tz=_AMS,
        solar_slots=solar, solar_source_name="Mock", load_profile=_flat_profile(400.0))
    load_rows = [r for r in rows if r[1] == "load"]
    solar_rows = [r for r in rows if r[1] == "solar"]
    assert len(load_rows) == 100  # DST-aware grid
    assert len(solar_rows) == 1  # only the one provided slot aligned
    assert solar_rows[0][2] == noon_utc.isoformat()


# --- 400-day purge ------------------------------------------------------------------------------

def test_purge_ledger_at_400_days(tmp_path):
    # The ledger purges by target_start at the 400-day horizon (symmetric with observations):
    # an old target falls out; a recent one stays.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.ledger_append([
            ("2024-01-01T00:00:00+00:00", "solar", "2024-01-01T10:00:00+00:00",
             1.0, 2.0, 3.0, "s", None, None, 1),
            ("2026-07-10T00:00:00+00:00", "solar", "2026-07-10T10:00:00+00:00",
             4.0, 5.0, 6.0, "s", None, None, 1),
        ])
        cutoff = (datetime(2026, 7, 14, tzinfo=UTC) - timedelta(days=400)).isoformat()
        purged = await store.purge_ledger_older_than(cutoff)
        remaining = await store.ledger_between("solar", *_WIDE)
        return purged, remaining

    purged, remaining = asyncio.run(run())
    assert purged == 1  # only the 2024 target fell outside the 400-day horizon
    assert [r["target_start"] for r in remaining] == ["2026-07-10T10:00:00+00:00"]
