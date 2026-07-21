"""In-process login rate-limiting / lockout (design §9).

Per-USERNAME failure tracking for `POST /api/auth/login` ONLY — deliberately NOT middleware. Only
interactive password login needs anti-abuse: invite codes are 256-bit random values and onboarding
is one-shot, so neither is brute-forceable. After `max_failures` failed attempts inside `window`,
the submitted username is locked for `cooldown`; while locked the handler returns **429 +
Retry-After** *before* touching the Argon2 hash — so a locked account costs an attacker nothing and
reveals nothing (the response is the same generic 429 whether or not the user exists, because
tracking keys off the SUBMITTED username STRING, not a DB row).

Deliberately in-memory / single-process: sufficient at single-home scale (design §9), and it keeps
a cache/store dependency out of the auth hot path. The tracking map is CAPPED (`max_tracked`, LRU by
last activity) so a spray of distinct usernames can't grow memory without bound. The clock is
injectable (monotonic by default, so a wall-clock change can neither unlock nor extend a lockout).

Pure and side-effect-free apart from its own dict — unit-testable with a fake clock, no I/O.
"""
from __future__ import annotations

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    # Monotonic timestamps of recent failures, pruned to the sliding `window`.
    failures: deque[float] = field(default_factory=deque)
    locked_until: float | None = None


class LoginRateLimiter:
    """Tracks failed logins per username and locks out abusers. Not thread-safe by design — the
    ASGI app runs the login handler on a single event loop, so all calls are serialized there."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: float = 15 * 60,
        cooldown_seconds: float = 15 * 60,
        max_tracked: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self.cooldown = cooldown_seconds
        self.max_tracked = max_tracked
        self._clock = clock
        # OrderedDict as an LRU: the most-recently-touched key sits at the end; eviction pops the
        # front (oldest).
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    @staticmethod
    def _key(username: str) -> str:
        # Track case-insensitively: usernames are UNIQUE COLLATE NOCASE, so "Admin"/"admin" MUST
        # share one lockout bucket — else trivial case variation would bypass the limit.
        return username.strip().casefold()

    def _touch(self, key: str) -> _Bucket:
        b = self._buckets.get(key)
        if b is None:
            b = _Bucket()
            self._buckets[key] = b
        self._buckets.move_to_end(key)  # mark most-recently-used
        # Cap the map so a distinct-username spray can't grow memory unbounded.
        while len(self._buckets) > self.max_tracked:
            self._evict_one(protect=key)
        return b

    def _evict_one(self, *, protect: str) -> None:
        """Evict exactly ONE bucket to keep the map under `max_tracked`.

        Prefer the OLDEST bucket whose lockout is NOT still active — so a spray of fresh usernames
        can never push a genuinely locked-out victim out of the map early (evicting a locked bucket
        would silently unlock it before its cooldown, which is the whole point of the lock). A lock
        whose cooldown has already elapsed counts as inactive and is fair game. The just-touched key
        (`protect`) is never evicted — it is the attempt in flight. If EVERY other bucket is still
        actively locked (a pathological all-locked map), evicting the oldest of them is accepted:
        bounding memory wins, and the attacker still pays the full failure budget to re-lock it."""
        now = self._clock()
        oldest_other: str | None = None
        for k, bkt in self._buckets.items():  # OrderedDict iterates oldest-first (LRU order)
            if k == protect:
                continue
            if oldest_other is None:
                oldest_other = k  # remember the oldest fallback candidate
            if bkt.locked_until is None or bkt.locked_until <= now:
                del self._buckets[k]
                return
        if oldest_other is not None:  # all others actively locked → drop the oldest of them
            del self._buckets[oldest_other]

    def retry_after(self, username: str) -> int | None:
        """Seconds the caller must wait if `username` is currently locked, else `None`.

        Read-only in effect (never CREATES a bucket) so a bare check can't be used to grow the map;
        it does lazily clear a lock whose cooldown has elapsed (window/cooldown expiry unlocks)."""
        key = self._key(username)
        b = self._buckets.get(key)
        if b is None or b.locked_until is None:
            return None
        remaining = b.locked_until - self._clock()
        if remaining <= 0:
            # Cooldown elapsed — drop the lock and the now-stale failure history so the next
            # attempt starts from a clean slate.
            b.locked_until = None
            b.failures.clear()
            return None
        return max(1, math.ceil(remaining))

    def register_failure(self, username: str) -> bool:
        """Record one failed attempt. Returns True IFF this attempt just TRIPPED the lockout, so
        the caller can audit the lockout event exactly once (not on every later blocked attempt)."""
        key = self._key(username)
        now = self._clock()
        b = self._touch(key)
        # Forget failures older than the sliding window (this is what makes "window expiry" reset
        # the count without any timer).
        cutoff = now - self.window
        while b.failures and b.failures[0] <= cutoff:
            b.failures.popleft()
        b.failures.append(now)
        if b.locked_until is None and len(b.failures) >= self.max_failures:
            b.locked_until = now + self.cooldown
            return True
        return False

    def reset(self, username: str) -> None:
        """Clear all failure/lock state for `username` — called on a successful login."""
        self._buckets.pop(self._key(username), None)
