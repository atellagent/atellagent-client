import asyncio
from types import SimpleNamespace
import pytest
from atellagent_client.sdk.gateway import request_pool as module

class Client:
    def __init__(self):
        self.cookies={'old':'cookie'}
        self.is_closed=False
    async def aclose(self):
        self.is_closed=True

def pool():
    clients=[];generation=[1]
    def factory():
        client=Client();clients.append(client);return client
    return module.RequestClientPool(factory,lambda:generation[0]),clients,generation

@pytest.mark.asyncio
async def test_success_reuses_connection_without_cookies():
    p,clients,_=pool()
    async with p.borrow()as first:
        assert not first.cookies;first.cookies['reply']='must not persist'
    async with p.borrow()as second:
        assert second is first and not second.cookies
    assert len(clients)==1
    await p.aclose();assert first.is_closed

@pytest.mark.asyncio
async def test_active_requests_never_share_or_close_neighbors():
    p,clients,_=pool();entered=asyncio.Event();release=asyncio.Event()
    async def neighbor():
        async with p.borrow()as c:
            entered.set();await release.wait();assert not c.is_closed
    task=asyncio.create_task(neighbor());await entered.wait()
    with pytest.raises(ValueError):
        async with p.borrow()as failed:
            raise ValueError('synthetic failure')
    assert failed.is_closed and not clients[0].is_closed
    release.set();await task;await p.aclose()

@pytest.mark.asyncio
async def test_cancelled_loan_is_closed_without_cancelling_neighbor():
    p,clients,_=pool();entered=asyncio.Event()
    async def call():
        async with p.borrow():entered.set();await asyncio.Event().wait()
    task=asyncio.create_task(call());await entered.wait()
    async with p.borrow()as neighbor:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert clients[0].is_closed and not neighbor.is_closed
    await p.aclose()

@pytest.mark.asyncio
async def test_material_change_discards_idle_and_late_active_connections():
    p,clients,g=pool()
    async with p.borrow()as old:g[0]=2
    assert old.is_closed
    async with p.borrow()as newer:assert newer is not old
    g[0]=3
    async with p.borrow()as newest:assert newest is not newer and newer.is_closed
    await p.aclose()

@pytest.mark.asyncio
@pytest.mark.parametrize('age,idle',[(3,1),(1,3),(-1,0)])
async def test_bounded_age_and_clock_rollback_fail_to_reuse(monkeypatch,age,idle):
    clock=[100.0];monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    p,clients,_=pool()
    async with p.borrow()as old:pass
    p._idle[0].created=100-age;p._idle[0].returned=100-idle
    async with p.borrow()as new:assert new is not old and old.is_closed
    await p.aclose()

@pytest.mark.asyncio
async def test_maximum_idle_clients_and_shutdown_are_bounded():
    p,clients,_=pool();entered=0;all_entered=asyncio.Event();release=asyncio.Event()
    async def call():
        nonlocal entered
        async with p.borrow():
            entered+=1
            if entered==7:all_entered.set()
            await release.wait()
    tasks=[asyncio.create_task(call())for _ in range(7)]
    await all_entered.wait();release.set();await asyncio.gather(*tasks)
    assert len(p._idle)==4 and sum(c.is_closed for c in clients)==3
    await p.aclose();assert all(c.is_closed for c in clients)
    with pytest.raises(RuntimeError):
        async with p.borrow():pass

@pytest.mark.asyncio
async def test_shutdown_during_active_loan_never_returns_it_to_pool():
    p,clients,_=pool()
    async with p.borrow()as c:
        await p.aclose();assert c.is_closed
    assert not p._idle and not p._active

@pytest.mark.asyncio
async def test_generation_read_failure_closes_completed_loan():
    p,clients,g=pool()
    async with p.borrow()as c:
        p._generation=lambda:(_ for _ in ()).throw(FileNotFoundError('removed cert'))
    assert c.is_closed and not p._idle

@pytest.mark.asyncio
async def test_cancelled_stale_cleanup_retains_close_ownership_until_shutdown():
    p,clients,g=pool();closing=asyncio.Event();release=asyncio.Event()
    async with p.borrow()as old:pass
    async def slow_close():
        closing.set();await release.wait();old.is_closed=True
    old.aclose=slow_close;g[0]=2
    async def borrower():
        async with p.borrow():raise AssertionError('cancel before loan is published')
    task=asyncio.create_task(borrower());await closing.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert p._closing and not old.is_closed
    release.set();await p.aclose();assert old.is_closed and not p._closing
