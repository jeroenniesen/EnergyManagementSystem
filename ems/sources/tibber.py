"""Tibber day-ahead prices adapter (SPEC §6.2). Implements the PriceSource port by querying the
Tibber GraphQL API and expanding hourly `total` (€/kWh, energy+tax) into 15-min slots
(CLAUDE.md: NL is quarter-hourly; hourly auto-expands to 4×15min).

Read-only. The token comes from the environment (never committed). Network I/O is injectable so
tests run against recorded GraphQL payloads, never the live API.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from ems.sources.prices import SLOT, PriceSlot

_log = logging.getLogger("ems.sources.tibber")

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
    """PriceSource backed by Tibber. slots() degrades to [] (logged) on any failure so a bad token
    or outage yields 'no plan' rather than crashing the API (fail-safe)."""

    def __init__(
        self,
        token: str,
        *,
        tz: ZoneInfo | None = None,
        endpoint: str = ENDPOINT,
        home_index: int = 0,
        http_post: GraphQLPost | None = None,
    ) -> None:
        self.token = token
        self.tz = tz
        self.endpoint = endpoint
        self.home_index = home_index
        self._post = http_post or _default_post

    def slots(self) -> list[PriceSlot]:
        if not self.token:
            _log.warning("Tibber token not set; no prices")
            return []
        try:
            data = self._post(self.endpoint, self.token, {"query": PRICE_QUERY})
            return parse_price_info(data, self.home_index)
        except Exception as exc:
            _log.warning("Tibber price fetch failed (%s: %s); no prices",
                         type(exc).__name__, exc)
            return []
