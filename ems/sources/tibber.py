"""Tibber day-ahead prices adapter (SPEC §6.2). Implements the PriceSource port by querying the
Tibber GraphQL API and expanding hourly `total` (€/kWh, energy+tax) into 15-min slots
(CLAUDE.md: NL is quarter-hourly; hourly auto-expands to 4×15min).

Read-only. The token comes from the environment (never committed). Network I/O is injectable so
tests run against recorded GraphQL payloads, never the live API.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
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
# How long a persisted snapshot is kept for warm-start (so a restart doesn't immediately refetch).
# Read via get_with_age (ignores expiry); the TTL only governs eventual purge housekeeping.
_PERSIST_TTL = timedelta(days=7)
_CACHE_KEY = "tibber:prices"


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
# priceInfo.today/tomorrow are hourly {total (€/kWh), startsAt (ISO, tz-aware)}.
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


def parse_price_info(data: dict, home_index: int = 0) -> list[PriceSlot]:
    """Pure parser: GraphQL `data` -> 15-min PriceSlots (today then tomorrow), sorted by start.
    Tolerant of missing pieces (returns what it can / empty)."""
    homes = (((data or {}).get("viewer") or {}).get("homes")) or []
    if not homes or not (0 <= home_index < len(homes)):
        return []
    info = ((homes[home_index] or {}).get("currentSubscription") or {}).get("priceInfo") or {}
    out: list[PriceSlot] = []
    for entry in (info.get("today") or []) + (info.get("tomorrow") or []):
        total, starts_at = entry.get("total"), entry.get("startsAt")
        if total is None or not starts_at:
            continue
        try:
            out.extend(_expand_hour(total, starts_at))
        except (ValueError, TypeError) as exc:
            # Skip a single malformed entry rather than discarding the whole response.
            _log.warning("skipping malformed Tibber price entry %s: %s", entry, exc)
    out.sort(key=lambda s: s.start)
    return out


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
        remaining = self._cache_ttl.total_seconds() - age
        if remaining > 0:
            self._next_fetch_at = self._clock() + timedelta(seconds=remaining)
        # else: leave _next_fetch_at None so the first slots() refetches (once), keeping last-good.

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
                    self._next_fetch_at = now + self._cache_ttl
                    self._persist(parsed)
                else:  # empty (no error): keep any last-good prices, retry soon
                    self._next_fetch_at = now + self._retry_ttl
                return self._cached
            except Exception as exc:
                # Fail-safe: keep serving the last good prices; back off so we don't hammer a 429.
                self._next_fetch_at = now + self._retry_ttl
                _log.warning("Tibber price fetch failed (%s: %s); %s", type(exc).__name__, exc,
                             "serving cached prices" if self._cached else "no prices yet")
                return self._cached
