"""Unit tests for the login rate-limiter / lockout (design §9/§10 anti-abuse).

Uses an injected fake clock so no test ever sleeps; asserts lockout-after-N, Retry-After, success
reset, per-username independence, window expiry, and the LRU map cap."""
from __future__ import annotations

from ems.web.ratelimit import LoginRateLimiter


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _limiter(clock: _Clock, **over) -> LoginRateLimiter:
    kwargs = dict(max_failures=5, window_seconds=900.0, cooldown_seconds=900.0, max_tracked=1000)
    kwargs.update(over)
    return LoginRateLimiter(clock=clock, **kwargs)


def test_not_locked_before_the_threshold():
    clock = _Clock()
    rl = _limiter(clock)
    for _ in range(4):  # one below the max
        tripped = rl.register_failure("alice")
        assert tripped is False
    assert rl.retry_after("alice") is None


def test_locks_out_after_n_failures_and_reports_retry_after():
    clock = _Clock()
    rl = _limiter(clock)
    tripped = [rl.register_failure("alice") for _ in range(5)]
    assert tripped[:4] == [False, False, False, False]
    assert tripped[4] is True  # the 5th failure trips the lock exactly once
    retry = rl.retry_after("alice")
    assert retry is not None
    assert 0 < retry <= 900  # full cooldown remaining, in seconds


def test_retry_after_counts_down_and_unlocks_after_cooldown():
    clock = _Clock()
    rl = _limiter(clock)
    for _ in range(5):
        rl.register_failure("alice")
    clock.advance(300)
    mid = rl.retry_after("alice")
    assert mid is not None and mid <= 600  # ~600s left
    clock.advance(600)  # cooldown fully elapsed
    assert rl.retry_after("alice") is None  # window/cooldown expiry unlocks


def test_success_resets_the_counter():
    clock = _Clock()
    rl = _limiter(clock)
    for _ in range(4):
        rl.register_failure("alice")
    rl.reset("alice")  # a successful login clears the history
    # A single failure afterward is nowhere near the threshold again.
    assert rl.register_failure("alice") is False
    assert rl.retry_after("alice") is None


def test_usernames_are_independent():
    clock = _Clock()
    rl = _limiter(clock)
    for _ in range(5):
        rl.register_failure("alice")
    assert rl.retry_after("alice") is not None
    assert rl.retry_after("bob") is None  # bob untouched
    assert rl.register_failure("bob") is False


def test_tracking_is_case_insensitive():
    # Usernames are UNIQUE COLLATE NOCASE — case variation must not create a fresh bucket.
    clock = _Clock()
    rl = _limiter(clock)
    for name in ("Alice", "ALICE", "alice", "aLiCe", "  alice  "):
        rl.register_failure(name)
    assert rl.retry_after("alice") is not None  # all five landed in one bucket → locked


def test_window_expiry_forgets_old_failures():
    clock = _Clock()
    rl = _limiter(clock)
    for _ in range(4):
        rl.register_failure("alice")
    clock.advance(901)  # push those four outside the 900s window
    # A fresh failure is now the ONLY one in the window → no lock.
    assert rl.register_failure("alice") is False
    assert rl.retry_after("alice") is None


def test_map_is_capped_evicting_the_oldest():
    clock = _Clock()
    rl = _limiter(clock, max_tracked=3)
    for name in ("a", "b", "c"):
        rl.register_failure(name)
    assert len(rl._buckets) == 3
    rl.register_failure("d")  # overflow → oldest ("a") evicted
    assert len(rl._buckets) == 3
    assert "a" not in rl._buckets
    assert {"b", "c", "d"} <= set(rl._buckets)


def test_eviction_never_unlocks_a_locked_victim_under_a_spray():
    # A locked-out victim must survive a full max_tracked spray of DISTINCT fresh usernames: the LRU
    # cap must prefer evicting unlocked buckets, never unlock the victim by evicting its bucket.
    clock = _Clock()
    rl = _limiter(clock, max_tracked=5)
    for _ in range(5):
        rl.register_failure("victim")  # trip the lockout
    assert rl.retry_after("victim") is not None  # locked
    # Spray far more than max_tracked distinct usernames, one failure each (all unlocked).
    for i in range(50):
        rl.register_failure(f"spray-{i}")
    assert len(rl._buckets) == 5  # cap held
    assert rl.retry_after("victim") is not None  # victim STILL locked (not evicted/unlocked)
    assert "victim" in rl._buckets


def test_eviction_prefers_expired_lock_over_active_lock():
    # When the oldest bucket's lock has EXPIRED (cooldown elapsed) it is fair game; a still-active
    # lock behind it must be preserved even though it is "older" than the key forcing eviction.
    clock = _Clock()
    rl = _limiter(clock, max_tracked=2)
    for _ in range(5):
        rl.register_failure("old-expired")  # locked at t0
    clock.advance(901)  # its cooldown has now elapsed (inactive lock)
    for _ in range(5):
        rl.register_failure("still-locked")  # freshly locked, active
    assert len(rl._buckets) == 2
    rl.register_failure("newcomer")  # overflow → evict the expired one, keep the active lock
    assert "old-expired" not in rl._buckets
    assert rl.retry_after("still-locked") is not None


def test_all_locked_map_still_bounds_memory():
    # Pathological: every tracked bucket is actively locked. Eviction must still bound memory
    # (drop the oldest) rather than grow past the cap.
    clock = _Clock()
    rl = _limiter(clock, max_tracked=3)
    for name in ("a", "b", "c"):
        for _ in range(5):
            rl.register_failure(name)  # all three locked
    assert all(rl.retry_after(n) is not None for n in ("a", "b", "c"))
    for _ in range(5):
        rl.register_failure("d")  # overflow with everything locked
    assert len(rl._buckets) == 3  # memory bounded despite the all-locked map


def test_retry_after_never_creates_a_bucket():
    # A bare check on an unknown username must not grow the map (else a check-spray leaks memory).
    clock = _Clock()
    rl = _limiter(clock)
    assert rl.retry_after("never-seen") is None
    assert len(rl._buckets) == 0
