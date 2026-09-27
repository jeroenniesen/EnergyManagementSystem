"""#126 — TibberPriceSource outage hysteresis + stable 'sinds hh:mm' (real source, not stubs)."""
from datetime import UTC, datetime, timedelta

from ems.sources.tibber import (
    _OUTAGE_GRACE,
    _OUTAGE_MIN_FAILURES,
    TibberPriceSource,
    _serialize_slots,
    parse_price_info,
)
from ems.tests.test_tibber import DATA, _Clock, _FakeCache


def _src(*, clock, http_post, **kw) -> TibberPriceSource:
    return TibberPriceSource(
        "tok",
        http_post=http_post,
        clock=clock,
        cache_ttl=timedelta(minutes=15),
        retry_ttl=timedelta(seconds=60),
        **kw,
    )


def test_failure_inside_cache_ttl_is_not_an_outage():
    """One blip while the 15‑min cache is still warm must not declare unavailable."""
    clock = _Clock(datetime(2026, 9, 27, 12, 0, tzinfo=UTC))
    n = {"ok": True}

    def post(url, token, body):
        if n["ok"]:
            return DATA
        raise RuntimeError("429")

    src = _src(clock=clock, http_post=post)
    assert len(src.slots()) == 12
    assert src.unavailable_since() is None

    n["ok"] = False
    clock.advance(minutes=16)  # past cache TTL → refetch fails
    src.slots()
    # Still within 2 h grace and only 1 failure → not an outage.
    assert src.unavailable_since() is None
    assert _OUTAGE_GRACE == timedelta(hours=2)
    assert _OUTAGE_MIN_FAILURES == 3


def test_outage_needs_grace_and_min_failures_then_freezes_sinds():
    """After 2 h + 3 consecutive failures, unavailable_since is the FIRST failure time."""
    clock = _Clock(datetime(2026, 9, 27, 12, 0, tzinfo=UTC))
    n = {"ok": True}

    def post(url, token, body):
        if n["ok"]:
            return DATA
        raise RuntimeError("down")

    src = _src(clock=clock, http_post=post)
    src.slots()  # success @ 12:00
    n["ok"] = False

    clock.advance(hours=2, minutes=1)  # past grace
    src.slots()  # failure 1 @ 14:01 — streak start frozen here
    first = datetime(2026, 9, 27, 14, 1, tzinfo=UTC)
    assert src.unavailable_since() is None  # only 1 failure

    clock.advance(seconds=61)
    src.slots()  # failure 2
    assert src.unavailable_since() is None

    clock.advance(seconds=61)
    src.slots()  # failure 3 → outage
    since = src.unavailable_since()
    assert since == first

    # Retries must NOT walk "sinds hh:mm" forward.
    clock.advance(seconds=61)
    src.slots()  # failure 4
    clock.advance(minutes=30)
    src.slots()  # failure 5
    assert src.unavailable_since() == first


def test_success_clears_outage():
    clock = _Clock(datetime(2026, 9, 27, 12, 0, tzinfo=UTC))
    n = {"ok": False}

    def post(url, token, body):
        if n["ok"]:
            return DATA
        raise RuntimeError("down")

    src = _src(clock=clock, http_post=post)
    # Cold start: 3 failures → outage (no grace without a prior success).
    for _ in range(3):
        src.slots()
        clock.advance(seconds=61)
    assert src.unavailable_since() is not None

    n["ok"] = True
    src.slots()
    assert src.unavailable_since() is None


def test_empty_response_counts_as_failure_toward_outage():
    clock = _Clock(datetime(2026, 9, 27, 12, 0, tzinfo=UTC))
    n = {"empty": False}

    def post(url, token, body):
        if n["empty"]:
            return {"viewer": {"homes": [{"currentSubscription": {"priceInfo": {}}}]}}
        return DATA

    src = _src(clock=clock, http_post=post)
    src.slots()
    n["empty"] = True
    clock.advance(hours=2, minutes=1)
    for _ in range(3):
        src.slots()
        clock.advance(seconds=61)
    assert src.unavailable_since() is not None


def test_warm_start_stale_snapshot_grace_measured_from_fetch_age():
    """A day-old warm-start counts as last_ok far in the past — grace already elapsed."""
    snapshot = _serialize_slots(parse_price_info(DATA))
    # age = 3 h → last_ok is 3 h ago (past 2 h grace); first refetch fails.
    cache = _FakeCache(preset={"tibber:prices": snapshot}, age=3 * 3600.0)
    clock = _Clock(datetime(2026, 9, 27, 15, 0, tzinfo=UTC))

    def boom(url, token, body):
        raise RuntimeError("down")

    src = _src(clock=clock, http_post=boom, cache_store=cache)
    for _ in range(3):
        src.slots()
        clock.advance(seconds=61)
    assert src.unavailable_since() is not None


def test_one_failure_after_grace_alone_is_not_outage():
    """Hysteresis: grace elapsed but only one failure → still None."""
    clock = _Clock(datetime(2026, 9, 27, 12, 0, tzinfo=UTC))
    n = {"ok": True}

    def post(url, token, body):
        if n["ok"]:
            return DATA
        raise RuntimeError("blip")

    src = _src(clock=clock, http_post=post)
    src.slots()
    n["ok"] = False
    clock.advance(hours=2, minutes=5)
    src.slots()
    assert src.unavailable_since() is None
