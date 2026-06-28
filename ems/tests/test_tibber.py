import threading
import time
from datetime import UTC, datetime, timedelta

from ems.sources.tibber import TibberPriceSource, _serialize_slots, parse_price_info


class _FakeCache:
    """In-memory CacheStore double: presettable snapshot + fixed reported age; captures sets."""

    def __init__(self, preset: dict | None = None, age: float = 0.0) -> None:
        self.data = dict(preset or {})
        self.age = age
        self.sets: list[tuple] = []

    def get_with_age(self, key):
        return (self.data[key], self.age) if key in self.data else None

    def set(self, key, value, ttl_seconds):
        self.data[key] = value
        self.sets.append((key, value, ttl_seconds))


class _Clock:
    """A controllable clock for TTL tests."""

    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


# Shape of a real Tibber priceInfo response (trimmed): hourly total in EUR/kWh + tz-aware startsAt.
DATA = {
    "viewer": {
        "homes": [
            {
                "currentSubscription": {
                    "priceInfo": {
                        "today": [
                            {"total": 0.2412, "startsAt": "2026-06-28T00:00:00+02:00"},
                            {"total": 0.1987, "startsAt": "2026-06-28T01:00:00+02:00"},
                        ],
                        "tomorrow": [
                            {"total": 0.3055, "startsAt": "2026-06-29T00:00:00+02:00"},
                        ],
                    }
                }
            }
        ]
    }
}


def test_parse_expands_each_hour_into_four_15min_slots():
    slots = parse_price_info(DATA)
    assert len(slots) == 3 * 4  # 2 today + 1 tomorrow hours, each -> 4 quarter-hours
    # First hour expands to :00/:15/:30/:45, all at the same price.
    first4 = slots[:4]
    assert [s.start.minute for s in first4] == [0, 15, 30, 45]
    assert all(s.eur_per_kwh == 0.2412 for s in first4)
    # tz-aware, sorted ascending, 15-min spacing.
    assert all(s.start.tzinfo is not None for s in slots)
    assert slots == sorted(slots, key=lambda s: s.start)
    assert (slots[1].start - slots[0].start).total_seconds() == 900


def test_parse_tolerates_missing_pieces():
    assert parse_price_info({}) == []
    assert parse_price_info({"viewer": {"homes": []}}) == []
    assert parse_price_info({"viewer": {"homes": [{}]}}) == []
    assert parse_price_info(DATA, home_index=-1) == []  # negative index not silently last-home


def test_parse_skips_one_malformed_entry_but_keeps_the_rest():
    bad = {
        "viewer": {"homes": [{"currentSubscription": {"priceInfo": {"today": [
            {"total": "not-a-number", "startsAt": "2026-06-28T00:00:00+02:00"},
            {"total": 0.20, "startsAt": "2026-06-28T01:00:00+02:00"},
        ], "tomorrow": []}}}]}
    }
    slots = parse_price_info(bad)
    assert len(slots) == 4  # only the good hour survives
    assert all(s.eur_per_kwh == 0.20 for s in slots)


def test_source_uses_injected_transport():
    calls = {}

    def fake_post(url, token, body):
        calls["url"], calls["token"], calls["body"] = url, token, body
        return DATA

    src = TibberPriceSource("tok-123", http_post=fake_post)
    slots = src.slots()
    assert len(slots) == 12
    assert calls["token"] == "tok-123"
    assert "priceInfo" in calls["body"]["query"]


def test_empty_token_returns_no_slots():
    assert TibberPriceSource("").slots() == []


def test_graphql_error_degrades_to_empty():
    def boom(url, token, body):
        raise RuntimeError("Tibber GraphQL error: invalid token")

    assert TibberPriceSource("bad", http_post=boom).slots() == []  # graceful, no raise


def test_repeated_calls_hit_the_network_only_once_within_the_ttl():
    n = {"c": 0}

    def counting_post(url, token, body):
        n["c"] += 1
        return DATA

    clock = _Clock(datetime(2026, 6, 28, 12, 0, tzinfo=UTC))
    src = TibberPriceSource("tok", http_post=counting_post, clock=clock,
                            cache_ttl=timedelta(minutes=15))
    for _ in range(10):  # a burst of dashboard polls
        assert len(src.slots()) == 12
    assert n["c"] == 1  # cached — only one Tibber request despite 10 calls

    clock.advance(minutes=16)  # past the TTL
    src.slots()
    assert n["c"] == 2  # refetched once the cache expired (e.g. to pick up tomorrow)


def test_429_serves_last_good_prices_instead_of_dropping_them():
    # First call succeeds and caches; later Tibber starts 429ing. The source must keep serving the
    # cached day-ahead prices, not collapse the plan/prediction to nothing.
    state = {"fail": False}

    def flaky_post(url, token, body):
        if state["fail"]:
            raise RuntimeError("Client error '429 Too Many Requests'")
        return DATA

    clock = _Clock(datetime(2026, 6, 28, 12, 0, tzinfo=UTC))
    src = TibberPriceSource("tok", http_post=flaky_post, clock=clock,
                            cache_ttl=timedelta(minutes=15))
    assert len(src.slots()) == 12  # good fetch, cached
    state["fail"] = True
    clock.advance(minutes=16)  # force a refetch attempt -> it 429s
    assert len(src.slots()) == 12  # last-good prices, NOT []


def test_first_ever_failure_returns_empty_then_recovers():
    state = {"fail": True}
    n = {"c": 0}

    def flaky_post(url, token, body):
        n["c"] += 1
        if state["fail"]:
            raise RuntimeError("429")
        return DATA

    clock = _Clock(datetime(2026, 6, 28, 12, 0, tzinfo=UTC))
    src = TibberPriceSource("tok", http_post=flaky_post, clock=clock,
                            retry_ttl=timedelta(seconds=60))
    assert src.slots() == []  # no cache yet -> empty (fail-safe)
    assert src.slots() == []  # within the retry backoff: does NOT re-hit the API (no 429 storm)
    assert n["c"] == 1  # only the first attempt hit the network
    state["fail"] = False
    clock.advance(seconds=61)  # past the retry backoff
    assert len(src.slots()) == 12  # recovers on the next allowed fetch
    assert n["c"] == 2


def test_single_flight_one_fetch_under_concurrent_cache_miss():
    """A dashboard refresh fans out to several threadpool workers; on a cache miss they all call
    slots() at once. Single-flight must collapse that into ONE upstream request (no 429 storm)."""
    n = {"c": 0}
    barrier = threading.Barrier(8)

    def slow_post(url, token, body):
        n["c"] += 1
        time.sleep(0.05)  # widen the race window so concurrent callers genuinely overlap
        return DATA

    src = TibberPriceSource("tok", http_post=slow_post)  # fresh: first call is a miss
    results: list[int] = []

    def worker():
        barrier.wait()
        results.append(len(src.slots()))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [12] * 8  # everyone got prices
    assert n["c"] == 1  # ...from a single fetch


def test_warm_start_serves_persisted_snapshot_without_refetch():
    snapshot = _serialize_slots(parse_price_info(DATA))
    cache = _FakeCache(preset={"tibber:prices": snapshot}, age=60.0)  # 60s old, TTL 15min
    n = {"c": 0}

    def counting_post(url, token, body):
        n["c"] += 1
        return DATA

    src = TibberPriceSource("tok", http_post=counting_post, cache_store=cache,
                            cache_ttl=timedelta(minutes=15))
    assert len(src.slots()) == 12  # served from the warm-started snapshot
    assert n["c"] == 0  # no network call — restart did not refetch


def test_stale_snapshot_triggers_exactly_one_refetch_and_persists():
    snapshot = _serialize_slots(parse_price_info(DATA))
    cache = _FakeCache(preset={"tibber:prices": snapshot}, age=3600.0)  # 1h old, past the TTL
    n = {"c": 0}

    def counting_post(url, token, body):
        n["c"] += 1
        return DATA

    src = TibberPriceSource("tok", http_post=counting_post, cache_store=cache,
                            cache_ttl=timedelta(minutes=15))
    assert len(src.slots()) == 12
    assert n["c"] == 1  # stale snapshot → one fresh fetch
    assert any(k == "tibber:prices" for k, _, _ in cache.sets)  # and the fresh result is persisted
