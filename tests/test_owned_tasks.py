"""Independent lifecycle work retains all owners through errors and cancellation."""
import asyncio
import pytest
from atellagent_client.runtime.owned_tasks import join_owned


@pytest.mark.asyncio
async def test_failure_waits_for_sibling_cleanup():
    entered, release = asyncio.Event(), asyncio.Event()
    finished = []
    async def failure():
        raise ValueError("owned failure")
    async def sibling():
        entered.set()
        await release.wait()
        finished.append(True)
    task = asyncio.create_task(join_owned(failure(), sibling()))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(ValueError, match="owned failure"):
        await task
    assert finished == [True]


@pytest.mark.asyncio
async def test_cancel_joins_each_owned_finalizer():
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finished = []
    async def operation():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cleaning.set()
            await release.wait()
            finished.append(True)
    task = asyncio.create_task(join_owned(operation()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    try:
        await asyncio.wait_for(cleaning.wait(), 1)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished == [True]
