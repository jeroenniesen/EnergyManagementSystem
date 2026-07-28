import asyncio

import pytest

from ems.storage.context import StorageContext


class _AsyncStore:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


def test_from_existing_allows_optional_stores_and_close_is_idempotent():
    history = _AsyncStore()
    ctx = StorageContext.from_existing(history=history)
    assert ctx.history is history
    assert ctx.settings is None
    asyncio.run(ctx.close())
    asyncio.run(ctx.close())
    assert history.closed == 1


def test_from_existing_rejects_unknown_store():
    with pytest.raises(TypeError, match="unknown storage"):
        StorageContext.from_existing(nope=object())
