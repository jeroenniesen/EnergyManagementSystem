"""F1 — device I/O quiesce around writes. While a SetData write sequence is in flight (plus a short
settle tail) the cluster reader serves its last cached value instead of piling reads onto the
Indevolt's single embedded HTTP server — the diagnosed charge-fails-under-car-load root cause. No
hardware — every client/driver gets a stub transport (CLAUDE.md)."""
import threading
import time

import pytest

from ems.domain import PhysicalMode
from ems.sources.battery import BatteryWriteUnconfirmed
from ems.sources.indevolt import DeviceQuiesce, IndevoltClusterReader, IndevoltReadClient
from ems.sources.indevolt_driver import IndevoltBatteryDriver

_READ = {"6002": 50, "6000": 0, "6001": 1000, "7101": 1, "606": 1000, "142": 5}


class _FakeReader:
    """Read stub for the driver's OWN reader (current_mode/probe) — never hits hardware and is not
    the cluster reader under test."""

    def read_keys(self, keys):
        src = {"7101": 1, "6001": 1000, "142": 5.0, "7120": 1000}
        return {str(k): src[str(k)] for k in keys if str(k) in src}


def _counting_reader(clk, reads, quiesce, *, cache_seconds=10.0):
    def read_post(_keys):
        reads["n"] += 1
        return dict(_READ)

    return IndevoltClusterReader(
        [IndevoltReadClient("m", rpc_post=read_post)],
        cache_seconds=cache_seconds, clock=lambda: clk[0], quiesce=quiesce,
    )


def test_read_during_inflight_write_serves_cache_no_device_io():
    clk = [0.0]
    reads = {"n": 0}
    quiesce = DeviceQuiesce(settle_seconds=4.0, clock=lambda: clk[0])
    reader = _counting_reader(clk, reads, quiesce)
    reader.read_towers()  # prime the cache — device read #1
    assert reads["n"] == 1
    clk[0] = 11.0  # coalesce window expired: a read would normally hit the device now

    def write_post(_point, _values):
        # Mid-write: a read must serve cache, NOT add a round-trip to the busy device.
        reader.read_towers()
        return {"result": True}

    driver = IndevoltBatteryDriver("m", armed=True, reader=_FakeReader(), rpc_post=write_post,
                                   quiesce=quiesce, write_retry_backoff=0)
    assert driver.apply(PhysicalMode.AUTO) is True
    assert reads["n"] == 1  # NO extra device read happened during the in-flight write


def test_settle_tail_blocks_read_at_t_plus_2s_but_not_t_plus_6s():
    clk = [0.0]
    reads = {"n": 0}
    quiesce = DeviceQuiesce(settle_seconds=4.0, clock=lambda: clk[0])
    reader = _counting_reader(clk, reads, quiesce)
    reader.read_towers()
    assert reads["n"] == 1
    clk[0] = 11.0  # coalesce expired; run a write that completes at t=11 → quiet_until = 15
    driver = IndevoltBatteryDriver("m", armed=True, reader=_FakeReader(),
                                   rpc_post=lambda _p, _v: {"result": True},
                                   quiesce=quiesce, write_retry_backoff=0)
    driver.apply(PhysicalMode.AUTO)
    clk[0] = 13.0  # write_end + 2s: inside the settle tail → serve cache, no device read
    reader.read_towers()
    assert reads["n"] == 1
    clk[0] = 17.0  # write_end + 6s: settle tail elapsed → a real device read happens
    reader.read_towers()
    assert reads["n"] == 2


def test_write_acquires_even_during_a_concurrent_read_burst():
    # Faithful to the deployment: reads + writes run on separate threads (asyncio.to_thread). A
    # running read burst must not prevent the write from acquiring/landing (eventual, no deadlock).
    quiesce = DeviceQuiesce(settle_seconds=0.0)  # real monotonic clock
    reads = {"n": 0}

    def read_post(_keys):
        reads["n"] += 1
        time.sleep(0.0005)
        return dict(_READ)

    reader = IndevoltClusterReader([IndevoltReadClient("m", rpc_post=read_post)],
                                   cache_seconds=0.0, quiesce=quiesce)  # never coalesces
    stop = threading.Event()

    def burst():
        while not stop.is_set():
            reader.read_towers()

    t = threading.Thread(target=burst)
    t.start()
    wrote = {"n": 0}

    def write_post(_point, _values):
        wrote["n"] += 1
        return {"result": True}

    driver = IndevoltBatteryDriver("m", armed=True, reader=_FakeReader(), rpc_post=write_post,
                                   quiesce=quiesce, write_retry_backoff=0)
    try:
        assert driver.apply(PhysicalMode.AUTO) is True  # the write lands despite the read burst
        assert wrote["n"] == 1
    finally:
        stop.set()
        t.join()


def test_standalone_reader_without_shared_quiesce_behaves_as_before():
    # No shared lock passed → the reader builds its own; coalescing + reads are exactly as today
    # (F1 is inert without a writer sharing the lock).
    clk = [0.0]
    reads = {"n": 0}

    def read_post(_keys):
        reads["n"] += 1
        return dict(_READ)

    reader = IndevoltClusterReader([IndevoltReadClient("m", rpc_post=read_post)],
                                   cache_seconds=10.0, clock=lambda: clk[0])  # quiesce defaulted
    reader.read_towers()
    reader.read_towers()  # coalesced
    assert reads["n"] == 1
    clk[0] = 11.0
    reader.read_towers()  # window expired → fresh read
    assert reads["n"] == 2


def test_shared_quiesce_leaves_timeout_retry_semantics_unchanged():
    # A write that keeps timing out still RAISES BatteryWriteUnconfirmed (hold, don't revert) even
    # with a shared quiesce — F1 must not swallow the driver's existing retry/hold contract. And the
    # in-flight count is released, so reads are not left starved after the timeout.
    quiesce = DeviceQuiesce(settle_seconds=4.0)

    def write_post(_point, _values):
        raise TimeoutError("slow")

    driver = IndevoltBatteryDriver("m", armed=True, reader=_FakeReader(), rpc_post=write_post,
                                   quiesce=quiesce, write_retry_backoff=0, write_attempts=2)
    with pytest.raises(BatteryWriteUnconfirmed):
        driver.apply(PhysicalMode.CHARGE, target_soc=90)
    assert quiesce._in_flight == 0  # released in the finally → reads recover after the settle tail
