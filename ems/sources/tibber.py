"""Tibber day-ahead prices adapter (SPEC §6.2). Implements the PriceSource port by querying the
Tibber GraphQL API and normalising `total` (€/kWh, energy+tax) into 15-min slots
(CLAUDE.md: NL is quarter-hourly; hourly auto-expands to 4×15min).

Read-only. The token comes from the environment (never committed). Network I/O is injectable so
tests run against recorded GraphQL payloads, never the live API.

Format note (#137): Tibber's `priceInfo` takes an optional `resolution` (`HOURLY` |
`QUARTER_HOURLY`). Omitting it defaults to **HOURLY** (back-compat since the 2025-09-30 MTU
change). The shipped query therefore receives hourly `today`/`tomorrow` entries and expands
each into four identical 15-min slots. The parser also accepts an already-quarter-hourly array
(e.g. `resolution: QUARTER_HOURLY`) without re-expanding — otherwise duplicates would make
completeness validation fail and leave the battery in HOLD/AUTO.
"""
from __future__ import annotations

import json
import logging
import statistics
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.sources.prices import SLOT, PriceSlot

_log = logging.getLogger("ems.sources.tibber")

# Day-ahead prices are static intraday (today is fixed; tomorrow appears ~13:00). The dashboard
# polls several price-consuming endpoints every few seconds, so without a cache we'd hammer Tibber
# into HTTP 429. Serve a cached copy within this window; refetch (e.g. to pick up tomorrow) after.
_CACHE_TTL = timedelta(minutes=15)
# After a failed/empty fetch, wait at least this long before trying again — short enough to recover
# quickly, long enough that a persistent 429 isn't hammered at the 5 s poll rate.
_RETRY_TTL = timedelta(seconds=60)
# #126 outage hysteresis (SPEC §6 keeps cached day-ahead usable; we do NOT treat cache TTL as
# "prices are dead"). Live control fails over to AUTO only after BOTH:
#   1. `_OUTAGE_GRACE` has elapsed since the last successful fetch, AND
#   2. at least `_OUTAGE_MIN_FAILURES` consecutive failed/empty fetches have been observed.
# Chosen so a single 429/blip after the 15‑min poll window cannot burn a mode switch + dwell.
_OUTAGE_GRACE = timedelta(hours=2)
_OUTAGE_MIN_FAILURES = 3
# How long a persisted snapshot is kept for warm-start (so a restart doesn't immediately refetch).
# Read via get_with_age (ignores expiry); the TTL only governs eventual purge housekeeping.
_PERSIST_TTL = timedelta(days=7)
_CACHE_KEY = "tibber:prices"

# Spacing thresholds for auto-detecting whether `today`/`tomorrow` entries are already
# quarter-hourly or still hourly (SPEC §6.2 / #137). Midpoint between 15 min and 60 min.
_QUARTER_SEC = SLOT.total_seconds()  # 900
_HOUR_SEC = 4 * _QUARTER_SEC  # 3600
_RESOLUTION_CUTOFF_SEC = (_QUARTER_SEC + _HOUR_SEC) / 2  # 2250


def _serialize_slots(slots: list[PriceSlot]) -> str:
    return json.dumps([{"s": s.start.isoformat(), "e": s.eur_per_kwh} for s in slots])


def _deserialize_slots(blob: str) -> list[PriceSlot]:
    try:
        raw = json.loads(blob)
        return [PriceSlot(start=datetime.fromisoformat(r["s"]), eur_per_kwh=float(r["e"]))
                for r in raw]
    except (ValueError, TypeError, KeyError):
        return []

ENDPOINT = "https://api.tibber.com/v1-beta/gql"
# priceInfo without `resolution` defaults to HOURLY (Tibber changelog 2025-09-30): each entry is
# {total (€/kWh), startsAt (ISO, tz-aware)} at :00. Parser expands to 4×15min; see #137.
PRICE_QUERY = (
    "{viewer{homes{currentSubscription{priceInfo{"
    "today{total startsAt} tomorrow{total startsAt}}}}}}"
)

# (url, token, graphql_body) -> the GraphQL `data` object. Raises on transport/GraphQL error.
GraphQLPost = Callable[[str, str, dict], dict]


def _default_post(url: str, token: str, body: dict, timeout: float = 12.0) -> dict:
    import httpx

    r = httpx.post(
        url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=timeout
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("errors"):
        raise RuntimeError(f"Tibber GraphQL error: {payload['errors']}")
    return payload.get("data") or {}


def _expand_hour(total: float, starts_at: str) -> list[PriceSlot]:
    """One hourly price -> four 15-min slots at :00/:15/:30/:45 (tz preserved from startsAt)."""
    start = datetime.fromisoformat(starts_at)
    return [PriceSlot(start=start + i * SLOT, eur_per_kwh=float(total)) for i in range(4)]


def _slot_from_entry(total: float, starts_at: str) -> PriceSlot:
    """One already-quarter-hourly price -> a single 15-min slot (no expansion)."""
    return PriceSlot(start=datetime.fromisoformat(starts_at), eur_per_kwh=float(total))


def _parse_starts(entries: Sequence[dict]) -> list[datetime]:
    out: list[datetime] = []
    for entry in entries:
        starts_at = entry.get("startsAt")
        if not starts_at:
            continue
        try:
            out.append(datetime.fromisoformat(starts_at))
        except (ValueError, TypeError):
            continue
    return out


def detect_price_resolution(entries: Sequence[dict]) -> str:
    """Return ``\"hourly\"`` or ``\"quarter_hourly\"`` from consecutive `startsAt` spacing.

    Tibber's default `priceInfo` (no `resolution` arg) is hourly; `resolution: QUARTER_HOURLY`
    yields ~15-min spacing. A single entry falls back to the minute field (:00 → hourly, else
    quarter-hourly) so we never expand a lone :15/:30/:45 point into four overlapping slots.
    Empty input defaults to hourly (the shipped query's contract).
    """
    starts = sorted(_parse_starts(entries))
    if len(starts) >= 2:
        deltas = [
            (b - a).total_seconds()
            for a, b in zip(starts, starts[1:], strict=False)
            if (b - a).total_seconds() > 0
        ]
        if deltas:
            median = statistics.median(deltas)
            return "quarter_hourly" if median < _RESOLUTION_CUTOFF_SEC else "hourly"
    if len(starts) == 1:
        return "hourly" if starts[0].minute == 0 else "quarter_hourly"
    return "hourly"


def _dedupe_slots(slots: list[PriceSlot]) -> list[PriceSlot]:
    """Keep one slot per `start` (last write wins), preserving chronological order."""
    by_start: dict[datetime, PriceSlot] = {}
    for slot in slots:
        by_start[slot.start] = slot
    return [by_start[k] for k in sorted(by_start)]


def parse_price_info(data: dict, home_index: int = 0) -> list[PriceSlot]:
    """Pure parser: GraphQL `data` -> 15-min PriceSlots (today then tomorrow), sorted by start.

    Auto-detects hourly vs quarter-hourly from `startsAt` spacing (#137): hourly entries expand
    to four identical 15-min slots; quarter-hourly entries pass through without re-expansion.
    Tolerant of missing pieces (returns what it can / empty). Duplicate `startsAt` values are
    collapsed so a mixed/retried payload cannot feed the planner overlapping quarters.
    """
    homes = (((data or {}).get("viewer") or {}).get("homes")) or []
    if not homes or not (0 <= home_index < len(homes)):
        return []
    info = ((homes[home_index] or {}).get("currentSubscription") or {}).get("priceInfo") or {}
    entries = list(info.get("today") or []) + list(info.get("tomorrow") or [])
    resolution = detect_price_resolution(entries)
    out: list[PriceSlot] = []
    for entry in entries:
        total, starts_at = entry.get("total"), entry.get("startsAt")
        if total is None or not starts_at:
            continue
        try:
            if resolution == "hourly":
                out.extend(_expand_hour(total, starts_at))
            else:
                out.append(_slot_from_entry(total, starts_at))
        except (ValueError, TypeError) as exc:
            # Skip a single malformed entry rather than discarding the whole response.
            _log.warning("skipping malformed Tibber price entry %s: %s", entry, exc)
    return _dedupe_slots(out)


class TibberPriceSource:
    """PriceSource backed by Tibber, with a TTL cache + last-good fallback (fail-safe).

    `slots()` returns cached prices within `cache_ttl` (so frequent dashboard polls make at most one
    request per window — avoids HTTP 429). On a fetch failure it serves the **last good** prices
    rather than dropping them — day-ahead prices for the rest of today don't change, so a transient
    outage/429 must not collapse the plan and prediction (CLAUDE.md: never worse than 'no EMS').
    Only a failure with no prior success degrades to []."""

    def __init__(
        self,
        token: str,
        *,
        tz: ZoneInfo | None = None,
        endpoint: str = ENDPOINT,
        home_index: int = 0,
        http_post: GraphQLPost | None = None,
        cache_ttl: timedelta = _CACHE_TTL,
        retry_ttl: timedelta = _RETRY_TTL,
        outage_grace: timedelta | None = None,
        outage_min_failures: int | None = None,
        clock: Callable[[], datetime] | None = None,
        cache_store: object | None = None,
        cache_key: str = _CACHE_KEY,
    ) -> None:
        self.token = token
        self.tz = tz
        self.endpoint = endpoint
        self.home_index = home_index
        self._post = http_post or _default_post
        self._cache_ttl = cache_ttl
        self._retry_ttl = retry_ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cached: list[PriceSlot] = []
        self._next_fetch_at: datetime | None = None  # earliest time we may hit the API again
        # #126: outage tracking for control fail-safe + "sinds hh:mm" badge. `_last_error_at` is
        # the FROZEN start of the current failure streak (first failure since last success) — never
        # overwritten on retries. Declaring an outage also needs grace + consecutive failures.
        self._last_ok_at: datetime | None = None
        self._last_error_at: datetime | None = None
        self._consecutive_failures: int = 0
        self._outage_grace = outage_grace if outage_grace is not None else _OUTAGE_GRACE
        self._outage_min_failures = (
            outage_min_failures if outage_min_failures is not None else _OUTAGE_MIN_FAILURES
        )
        # When the cached curve was last successfully fetched (warm-start or live). Drives prices
        # freshness so a last-good curve after an outage ages into STALE (issue #79).
        self.fetched_at: datetime | None = None
        # Single-flight: when the TTL lapses, only ONE concurrent caller fetches; the rest wait and
        # then read the now-fresh cache. Prevents a dashboard poll fan-out (sync endpoints run in
        # the threadpool) from firing several simultaneous Tibber requests → HTTP 429.
        self._lock = threading.Lock()
        self._cache_store = cache_store
        self._cache_key = cache_key
        self._warm_start()

    def _warm_start(self) -> None:
        """Seed the in-memory cache from a persisted snapshot so a restart doesn't immediately
        refetch. We keep the snapshot as last-good regardless of age; we skip the next fetch only if
        it's still within the TTL (older → fetch once on the first call, via single-flight)."""
        if self._cache_store is None:
            return
        try:
            got = self._cache_store.get_with_age(self._cache_key)
        except Exception:
            got = None
        if not got:
            return
        blob, age = got
        slots = _deserialize_slots(blob)
        if not slots:
            return
        self._cached = slots
        # Warm-start counts as a prior success at (now - age), so outage grace is measured from
        # when the cached curve was actually fetched — not from process start. Same stamp drives
        # prices freshness (issue #79).
        stamped = self._clock() - timedelta(seconds=max(0.0, float(age)))
        self._last_ok_at = stamped
        self.fetched_at = stamped
        remaining = self._cache_ttl.total_seconds() - age
        if remaining > 0:
            self._next_fetch_at = self._clock() + timedelta(seconds=remaining)
        # else: leave _next_fetch_at None so the first slots() refetches (once), keeping last-good.

    def _note_failure(self, now: datetime) -> None:
        """Record a failed/empty fetch. Freeze the streak-start timestamp on the first failure."""
        self._consecutive_failures += 1
        if self._last_error_at is None:
            self._last_error_at = now

    def _note_success(self, now: datetime) -> None:
        self._last_ok_at = now
        self._last_error_at = None
        self._consecutive_failures = 0

    def unavailable_since(self) -> datetime | None:
        """When live Tibber prices became unavailable for control, or None while still OK.

        Thresholds (#126 review — grace over rewriting SPEC §6):
        - Poll/cache TTL (`cache_ttl`, default 15 min) only throttles fetches; it does NOT mean
          day-ahead prices are dead (SPEC §6: cached Tibber slots stay usable).
        - Outage requires `_outage_grace` (default **2 h**) since last success AND
          `_outage_min_failures` (default **3**) consecutive failed/empty fetches.
        - Returned timestamp is the frozen first failure of the streak (`_last_error_at`), so
          "sinds hh:mm" does not walk forward on every retry.
        Cold start (never succeeded): grace is skipped; N consecutive failures are enough.
        """
        if self._consecutive_failures < self._outage_min_failures:
            return None
        if self._last_error_at is None:
            return None
        if self._last_ok_at is None:
            return self._last_error_at
        if self._clock() - self._last_ok_at <= self._outage_grace:
            return None
        return self._last_error_at

    def _persist(self, slots: list[PriceSlot]) -> None:
        if self._cache_store is None:
            return
        try:
            self._cache_store.set(
                self._cache_key, _serialize_slots(slots), _PERSIST_TTL.total_seconds()
            )
        except Exception:
            pass  # persistence is best-effort; never break a price read over it

    def slots(self) -> list[PriceSlot]:
        if not self.token:
            _log.warning("Tibber token not set; no prices")
            return []
        # Throttle: serve the cache until the next allowed fetch. One request per window regardless
        # of how often the dashboard polls — successes hold for cache_ttl, failures for retry_ttl.
        if self._next_fetch_at is not None and self._clock() < self._next_fetch_at:
            return self._cached
        with self._lock:
            # Double-checked: a concurrent caller may have refreshed while we waited on the lock.
            now = self._clock()
            if self._next_fetch_at is not None and now < self._next_fetch_at:
                return self._cached
            try:
                data = self._post(self.endpoint, self.token, {"query": PRICE_QUERY})
                parsed = parse_price_info(data, self.home_index)
                if parsed:
                    self._cached = parsed
                    self.fetched_at = now
                    self._next_fetch_at = now + self._cache_ttl
                    self._note_success(now)
                    self._persist(parsed)
                else:  # empty (no error): keep any last-good prices, retry soon
                    self._next_fetch_at = now + self._retry_ttl
                    self._note_failure(now)
                return self._cached
            except Exception as exc:
                # Fail-safe: keep serving the last good prices; back off so we don't hammer a 429.
                self._next_fetch_at = now + self._retry_ttl
                self._note_failure(now)
                _log.warning("Tibber price fetch failed (%s: %s); %s", type(exc).__name__, exc,
                             "serving cached prices" if self._cached else "no prices yet")
                return self._cached
