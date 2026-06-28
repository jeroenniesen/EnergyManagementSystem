"""CacheStore: TTL semantics, warm-start age, purge, persistence across instances."""
from __future__ import annotations

from ems.storage.cache import CacheStore
from ems.web.api import _bounded_put


def test_bounded_put_evicts_oldest_past_capacity():
    cache: dict = {}
    for i in range(5):
        _bounded_put(cache, f"k{i}", i, maxn=3)
    assert list(cache) == ["k2", "k3", "k4"]  # oldest two evicted, newest kept
    # re-inserting an existing newest key never evicts the entry we just need
    _bounded_put(cache, "k4", 99, maxn=3)
    assert cache["k4"] == 99 and len(cache) == 3


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _store(tmp_path, clock):
    s = CacheStore(str(tmp_path / "cache.sqlite"), clock=clock)
    s.init()
    return s


def test_set_then_get_within_ttl(tmp_path):
    clk = _Clock()
    s = _store(tmp_path, clk)
    s.set("k", "v", ttl_seconds=100)
    assert s.get("k") == "v"


def test_get_returns_none_after_expiry_and_drops_row(tmp_path):
    clk = _Clock()
    s = _store(tmp_path, clk)
    s.set("k", "v", ttl_seconds=100)
    clk.t += 101  # past the TTL
    assert s.get("k") is None
    assert s.count() == 0  # expired row was purged on read


def test_missing_key_is_none(tmp_path):
    assert _store(tmp_path, _Clock()).get("nope") is None


def test_set_overwrites_value_and_ttl(tmp_path):
    clk = _Clock()
    s = _store(tmp_path, clk)
    s.set("k", "old", ttl_seconds=10)
    s.set("k", "new", ttl_seconds=10)
    assert s.get("k") == "new"
    assert s.count() == 1  # upsert, not a second row


def test_get_with_age_ignores_expiry(tmp_path):
    clk = _Clock()
    s = _store(tmp_path, clk)
    s.set("snap", "payload", ttl_seconds=1)
    clk.t += 500  # well past TTL
    res = s.get_with_age("snap")
    assert res is not None
    value, age = res
    assert value == "payload" and abs(age - 500) < 1e-6


def test_purge_expired_counts_and_keeps_live(tmp_path):
    clk = _Clock()
    s = _store(tmp_path, clk)
    s.set("dead", "x", ttl_seconds=1)
    s.set("live", "y", ttl_seconds=10_000)
    clk.t += 5
    assert s.purge_expired() == 1
    assert s.get("live") == "y" and s.count() == 1


def test_breakdown_groups_live_rows_by_prefix(tmp_path):
    clk = _Clock()
    s = _store(tmp_path, clk)
    s.set("explain:a", "1", ttl_seconds=100)
    s.set("explain:b", "2", ttl_seconds=100)
    s.set("tibber:prices", "3", ttl_seconds=100)
    s.set("forecast_solar:slots", "4", ttl_seconds=1)
    clk.t += 5  # expire the forecast row → excluded from the live breakdown
    b = s.breakdown()
    assert b == {"total": 3, "explain": 2, "tibber": 1}


def test_persists_across_instances(tmp_path):
    clk = _Clock()
    path = str(tmp_path / "cache.sqlite")
    a = CacheStore(path, clock=clk)
    a.init()
    a.set("k", "v", ttl_seconds=10_000)
    # a fresh instance (mimics a process restart) still sees the value
    b = CacheStore(path, clock=clk)
    assert b.get("k") == "v"
