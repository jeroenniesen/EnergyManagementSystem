"""Live solar forecast via Solcast Hobbyist rooftop API (SPEC §6.3 / BACKLOG B-14).

Primary provider: real P10/P50/P90 percentiles from
`GET https://api.solcast.com.au/rooftop_sites/{resource_id}/forecasts?format=json`
(`pv_estimate` / `pv_estimate10` / `pv_estimate90` in kW, 30-min periods).

The EMS owns the refresh (call-budget ledger + daylight schedule) so a retry/dashboard fan-out
cannot burn the free Hobbyist 10 calls/day. On budget exhaustion, stale/missing cache, or any
fetch error the source **falls back** to the injected Forecast.Solar (or model) adapter — fail
safe, never worse than "no Solcast". Network I/O is injectable for canned-payload tests.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.sources.forecast import SLOT, SLOTS_PER_DAY, ForecastSlot, MockSolarForecastSource

_log = logging.getLogger("ems.sources.solcast")

JsonGet = Callable[[str, dict[str, str]], dict]

# SPEC §6.3 sample — 7 daylight refreshes, 3 spare on a 10/day Hobbyist budget.
DEFAULT_REFRESH_TIMES: tuple[str, ...] = (
    "07:00", "09:00", "11:00", "13:00", "15:00", "17:00", "19:00",
)
DEFAULT_DAILY_BUDGET = 10

# Persisted forecast warm-start (read via get_with_age; TTL only governs eventual purge).
_PERSIST_TTL_SECONDS = 12 * 3600.0
_CACHE_KEY = "solcast:slots"
_BUDGET_KEY = "solcast:budget"
# Budget row must survive the whole local day; long TTL + day key in the payload.
_BUDGET_TTL_SECONDS = 48 * 3600.0


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _deserialize_slots(blob: str) -> list[ForecastSlot]:
    try:
        raw = json.loads(blob)
        return [
            ForecastSlot(
                start=datetime.fromisoformat(r["s"]),
                p10_w=float(r["a"]),
                p50_w=float(r["b"]),
                p90_w=float(r["c"]),
            )
            for r in raw
        ]
    except (ValueError, TypeError, KeyError):
        return []


def _httpx_get(url: str, headers: dict[str, str], timeout: float) -> dict:
    import httpx

    r = httpx.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _parse_period_minutes(period: object) -> int:
    """ISO-8601 duration like PT30M → minutes. Defaults to 30 (Solcast Hobbyist)."""
    if not isinstance(period, str) or not period.startswith("PT") or not period.endswith("M"):
        return 30
    try:
        return max(1, int(period[2:-1]))
    except ValueError:
        return 30


def _ordered_watts(p10_kw: float, p50_kw: float, p90_kw: float) -> tuple[float, float, float]:
    """kW → W and enforce p10 ≤ p50 ≤ p90 (missing percentiles collapse to p50)."""
    p50_w = max(0.0, float(p50_kw) * 1000.0)
    p10_w = max(0.0, float(p10_kw) * 1000.0)
    p90_w = max(0.0, float(p90_kw) * 1000.0)
    lo, mid, hi = sorted((p10_w, p50_w, p90_w))
    return lo, mid, hi


def parse_forecasts(
    data: dict,
    tz: ZoneInfo,
    midnight: datetime,
    n_slots: int,
) -> list[ForecastSlot]:
    """Resample Solcast `forecasts[]` (30-min kW at period_end) onto a local 15-min grid.

    Each period covering [period_end − period, period_end) is held stepwise onto every 15-min slot
    start that falls inside that half-open interval. Slots outside the sampled range stay 0.
    """
    rows = (data or {}).get("forecasts") or []
    intervals: list[tuple[datetime, datetime, float, float, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        period_end_raw = row.get("period_end")
        p50 = row.get("pv_estimate")
        if period_end_raw is None or p50 is None:
            continue
        try:
            end = datetime.fromisoformat(str(period_end_raw).replace("Z", "+00:00"))
            end = end.astimezone(tz)
            minutes = _parse_period_minutes(row.get("period"))
            start = end - timedelta(minutes=minutes)
            p10_raw = row.get("pv_estimate10", p50)
            p90_raw = row.get("pv_estimate90", p50)
            p10_w, p50_w, p90_w = _ordered_watts(float(p10_raw), float(p50), float(p90_raw))
            intervals.append((start, end, p10_w, p50_w, p90_w))
        except (ValueError, TypeError):
            continue
    intervals.sort(key=lambda t: t[0])

    out: list[ForecastSlot] = []
    j = 0
    held = (0.0, 0.0, 0.0)
    active_end: datetime | None = None
    for i in range(n_slots):
        slot_start = midnight + i * SLOT
        while j < len(intervals) and intervals[j][0] <= slot_start:
            _s, active_end, *held_vals = intervals[j]
            held = (held_vals[0], held_vals[1], held_vals[2])
            j += 1
        if active_end is not None and slot_start < active_end:
            out.append(ForecastSlot(start=slot_start, p10_w=held[0], p50_w=held[1], p90_w=held[2]))
        else:
            out.append(ForecastSlot(start=slot_start, p10_w=0.0, p50_w=0.0, p90_w=0.0))
    return out


def _parse_hhmm(value: str) -> tuple[int, int] | None:
    try:
        hour_s, minute_s = value.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        return None
    return None


def refresh_due(
    now_local: datetime,
    last_fetch_local: datetime | None,
    refresh_times: Sequence[str] = DEFAULT_REFRESH_TIMES,
) -> bool:
    """True when EMS should spend a Solcast call (cold cache or a new daylight slot has arrived).

    Overnight warm cache is kept until the first scheduled time today — we do **not** burn a call
    just because the snapshot crossed local midnight.
    """
    if last_fetch_local is None:
        return True
    due: list[datetime] = []
    for raw in refresh_times:
        parsed = _parse_hhmm(raw)
        if parsed is None:
            continue
        hour, minute = parsed
        candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now_local:
            due.append(candidate)
    if not due:
        return False  # before first daylight slot — keep overnight cache
    latest_due = max(due)
    last = last_fetch_local.astimezone(now_local.tzinfo)
    return last < latest_due


class SolcastBudgetLedger:
    """Persisted daily Solcast call counter (SPEC §6.3). Resets at local midnight."""

    def __init__(
        self,
        cache_store: object | None,
        *,
        tz: ZoneInfo,
        daily_budget: int = DEFAULT_DAILY_BUDGET,
        clock: Callable[[], datetime] = _utcnow,
        cache_key: str = _BUDGET_KEY,
    ) -> None:
        self._store = cache_store
        self.tz = tz
        self.daily_budget = max(0, int(daily_budget))
        self._clock = clock
        self._cache_key = cache_key
        self._lock = threading.Lock()
        self._mem_day: str | None = None
        self._mem_count = 0

    def _today(self) -> str:
        return self._clock().astimezone(self.tz).date().isoformat()

    def _load(self) -> tuple[str, int]:
        today = self._today()
        if self._mem_day == today:
            return today, self._mem_count
        day, count = today, 0
        if self._store is not None:
            try:
                blob = self._store.get(self._cache_key)
                if blob:
                    raw = json.loads(blob)
                    if raw.get("day") == today:
                        day, count = today, int(raw.get("n", 0))
            except Exception:
                day, count = today, 0
        self._mem_day, self._mem_count = day, count
        return day, count

    def remaining(self) -> int:
        _day, count = self._load()
        return max(0, self.daily_budget - count)

    def can_call(self) -> bool:
        return self.remaining() > 0

    def record_call(self) -> int:
        """Increment today's counter; returns the new count. No-op past the cap."""
        with self._lock:
            day, count = self._load()
            if count >= self.daily_budget:
                return count
            count += 1
            self._mem_day, self._mem_count = day, count
            if self._store is not None:
                try:
                    self._store.set(
                        self._cache_key,
                        json.dumps({"day": day, "n": count}),
                        _BUDGET_TTL_SECONDS,
                    )
                except Exception:
                    pass
            return count


class SolcastSource:
    """SolarForecastSource backed by Solcast, with budget ledger + Forecast.Solar/model fallback."""

    def __init__(
        self,
        *,
        tz: ZoneInfo,
        api_key: str,
        resource_id: str,
        daily_budget: int = DEFAULT_DAILY_BUDGET,
        refresh_times: Sequence[str] = DEFAULT_REFRESH_TIMES,
        horizon_slots: int = 2 * SLOTS_PER_DAY,
        http_get: JsonGet | None = None,
        clock: Callable[[], datetime] = _utcnow,
        fallback: object | None = None,
        cache_store: object | None = None,
        cache_key: str = _CACHE_KEY,
        budget_ledger: SolcastBudgetLedger | None = None,
    ) -> None:
        self.tz = tz
        self.api_key = api_key
        self.resource_id = resource_id
        self.refresh_times = tuple(refresh_times) or DEFAULT_REFRESH_TIMES
        self.horizon_slots = horizon_slots
        self._clock = clock
        self._timeout = 12.0
        self._get = http_get or (
            lambda url, headers: _httpx_get(url, headers, self._timeout)
        )
        self._fallback = fallback or MockSolarForecastSource(
            tz, clock=clock, horizon_slots=horizon_slots
        )
        self._cache: tuple[datetime, list[ForecastSlot]] | None = None
        self._last_fetch_at: datetime | None = None
        self.issued_at: datetime | None = None
        self.provider = "solcast"
        self.source_label = "solcast"
        self._lock = threading.Lock()
        self._cache_store = cache_store
        self._cache_key = cache_key
        self.budget = budget_ledger or SolcastBudgetLedger(
            cache_store, tz=tz, daily_budget=daily_budget, clock=clock
        )
        self._warm_start()

    @property
    def url(self) -> str:
        return (
            f"https://api.solcast.com.au/rooftop_sites/"
            f"{self.resource_id}/forecasts?format=json"
        )

    def _warm_start(self) -> None:
        if self._cache_store is None:
            return
        try:
            got = self._cache_store.get_with_age(self._cache_key)
        except Exception:
            got = None
        if not got:
            return
        blob, age = got
        try:
            payload = json.loads(blob)
            slots = [
                ForecastSlot(
                    start=datetime.fromisoformat(r["s"]),
                    p10_w=float(r["a"]),
                    p50_w=float(r["b"]),
                    p90_w=float(r["c"]),
                )
                for r in payload["slots"]
            ]
            fetched = datetime.fromisoformat(payload["fetched_at"])
        except (ValueError, TypeError, KeyError):
            slots = _deserialize_slots(blob)
            fetched = self._clock() - timedelta(seconds=age)
        if slots:
            self._cache = (fetched, slots)
            self._last_fetch_at = fetched
            self.issued_at = fetched
            self.source_label = "solcast"
            self.provider = "solcast"

    def _persist(self, slots: list[ForecastSlot], fetched_at: datetime) -> None:
        if self._cache_store is None:
            return
        try:
            blob = json.dumps({
                "fetched_at": fetched_at.isoformat(),
                "slots": [{"s": s.start.isoformat(), "a": s.p10_w, "b": s.p50_w, "c": s.p90_w}
                          for s in slots],
            })
            self._cache_store.set(self._cache_key, blob, _PERSIST_TTL_SECONDS)
        except Exception:
            pass

    def _use_fallback(self, reason: str) -> list[ForecastSlot]:
        _log.warning("Solcast unavailable (%s); using forecast fallback", reason)
        slots = self._fallback.slots()
        # Prefer the fallback's own label when present (Forecast.Solar / model).
        label = getattr(self._fallback, "source_label", None) or "forecast_solar (fallback)"
        self.source_label = label if "fallback" in str(label) else f"{label} (fallback)"
        self.provider = "forecast_solar" if "forecast.solar" in str(label) else "fallback"
        # Do not overwrite a good Solcast cache — keep last-good for later schedule windows.
        return slots

    def _serve_cache(self, *, missed_refresh: bool = False) -> list[ForecastSlot] | None:
        if self._cache is None:
            return None
        # Keep issued_at at fetch time so freshness ages a days-old warm-start (#79).
        # Do NOT label a healthy overnight gap (19:00→07:00) as "cached" — only a missed due
        # refresh or a >24h-old snapshot (matches SIGNAL_STALE_AFTER_S["forecast"]).
        self.provider = "solcast"
        self.issued_at = self._last_fetch_at
        age_s = 0.0
        if self._last_fetch_at is not None:
            age_s = max(0.0, (self._clock() - self._last_fetch_at).total_seconds())
        if missed_refresh or age_s > 24 * 3600:
            self.source_label = "solcast (cached)"
        else:
            self.source_label = "solcast"
        return self._cache[1]

    def slots(self) -> list[ForecastSlot]:
        now = self._clock()
        local = now.astimezone(self.tz)
        due = refresh_due(local, self._last_fetch_at, self.refresh_times)

        if not due:
            cached = self._serve_cache()
            if cached is not None:
                return cached
            # No cache before first refresh — fall through for a cold fetch if budget allows.

        if not self.api_key or not self.resource_id:
            cached = self._serve_cache(missed_refresh=True)
            if cached is not None:
                return cached
            return self._use_fallback("missing api_key/resource_id")

        if not self.budget.can_call():
            cached = self._serve_cache(missed_refresh=True)
            if cached is not None:
                self.source_label = "solcast (budget held)"
                return cached
            return self._use_fallback("daily call budget exhausted")

        with self._lock:
            # Re-check under the lock: another thread may have refreshed / spent budget.
            now = self._clock()
            local = now.astimezone(self.tz)
            due = refresh_due(local, self._last_fetch_at, self.refresh_times)
            if not due:
                cached = self._serve_cache()
                if cached is not None:
                    return cached
            if not self.budget.can_call():
                cached = self._serve_cache(missed_refresh=True)
                if cached is not None:
                    self.source_label = "solcast (budget held)"
                    return cached
                return self._use_fallback("daily call budget exhausted")

            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            try:
                data = self._get(self.url, {"Authorization": f"Bearer {self.api_key}"})
                forecasts = (data or {}).get("forecasts") or []
                if not forecasts:
                    raise ValueError("Solcast returned empty forecasts")
                slots = parse_forecasts(data, self.tz, midnight, self.horizon_slots)
                self.budget.record_call()
                self._cache = (now, slots)
                self._last_fetch_at = now
                self.issued_at = now
                self.source_label = "solcast"
                self.provider = "solcast"
                self._persist(slots, now)
                return slots
            except Exception as exc:
                _log.warning(
                    "Solcast fetch failed (%s: %s)", type(exc).__name__, exc,
                )
                cached = self._serve_cache(missed_refresh=True)
                if cached is not None:
                    return cached
                return self._use_fallback(f"{type(exc).__name__}: {exc}")
