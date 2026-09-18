import asyncio

import pytest

from ems.storage.context import StorageContext


class _AsyncStore:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


class _SyncStore:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class _FailOnceStore:
    def __init__(self):
        self.closed = 0

    async def close(self):
        if self.closed == 0:
            self.closed += 1
            raise RuntimeError("transient")
        self.closed += 1


def test_from_existing_allows_optional_stores_and_close_is_idempotent():
    history = _AsyncStore()
    ctx = StorageContext.from_existing(history=history)
    assert ctx.history is history
    assert ctx.settings is None
    asyncio.run(ctx.close())
    asyncio.run(ctx.close())
    assert history.closed == 1


def test_close_accounts_for_sync_repositories_without_close_hooks():
    history = _AsyncStore()
    cache = _SyncStore()
    control_state = object()
    ctx = StorageContext.from_existing(history=history, cache=cache, control_state=control_state)

    asyncio.run(ctx.close())

    assert history.closed == 1
    assert cache.closed == 1
    assert ctx._closed
    assert ctx.close_errors == ()


def test_close_isolates_failures_and_retries_only_failed_store():
    failed = _FailOnceStore()
    later = _AsyncStore()
    ctx = StorageContext.from_existing(history=failed, audit=later)

    asyncio.run(ctx.close())
    assert failed.closed == 1
    assert later.closed == 1
    assert not ctx._closed
    assert ctx.close_errors and ctx.close_errors[0].startswith("history:")

    asyncio.run(ctx.close())
    assert failed.closed == 2
    assert later.closed == 1
    assert ctx._closed
    assert ctx.close_errors == ()


def test_from_existing_rejects_unknown_store():
    with pytest.raises(TypeError, match="unknown storage"):
        StorageContext.from_existing(nope=object())
