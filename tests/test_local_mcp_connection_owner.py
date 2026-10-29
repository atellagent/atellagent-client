"""Reusable local MCP contexts have one task that enters and exits them."""
import asyncio
from types import SimpleNamespace
import pytest
from atellagent_client.connected.mcp_client import LocalMCPClient, MCP_PROTOCOL_VERSION
from atellagent_client.sdk.config_models import BridgeDeploymentConfig


def owned_transport(monkeypatch, *, fail_call=False, pause_discovery=False, pause_close=False):
    import mcp
    import mcp.client.stdio
    entered, release, closing, finish = (asyncio.Event() for _ in range(4))
    opened, closed, calls = [], [], []
    class Client:
        def __init__(self, *_args, **_kwargs): self.session = self
        async def __aenter__(self):
            self.owner = asyncio.current_task();opened.append(self.owner)
            return self
        async def __aexit__(self, *_):
            assert asyncio.current_task() is self.owner, 'MCP context exited in a different task'
            closing.set()
            if pause_close: await finish.wait()
            closed.append(self.owner)
        async def send_discover(self, _):
            entered.set()
            if pause_discovery: await release.wait()
            return dict(resultType='complete', supportedVersions=[MCP_PROTOCOL_VERSION], cacheScope='private', ttlMs=0,
                capabilities={'experimental': {'atellagent/durableIdempotency': {'key':'atellagent/idempotencyKey','durable':True}}})
        async def list_tools(self, **kwargs):
            assert kwargs == {'cache_mode':'bypass'}
            return SimpleNamespace(tools=[])
        async def call_tool(self, *args, **kwargs):
            calls.append((args, kwargs))
            if fail_call: raise ValueError('owned original call failure')
            return SimpleNamespace(model_dump=lambda **_:{'content':[]})
    monkeypatch.setattr(mcp, 'Client', Client)
    monkeypatch.setattr(mcp.client.stdio, 'stdio_client', lambda _:object())
    target = LocalMCPClient(BridgeDeploymentConfig(target_transport='stdio', target_command='owned-unused'))
    return SimpleNamespace(target=target, entered=entered, release=release, closing=closing, finish=finish,
        opened=opened, closed=closed, calls=calls)


@pytest.mark.asyncio
async def test_explicit_close_from_another_task_exits_in_the_connection_owner(monkeypatch):
    c = owned_transport(monkeypatch)
    assert await c.target.manifest() == {'tools':[]}
    await asyncio.create_task(c.target.close())
    assert c.opened == c.closed and c.target._client is None


@pytest.mark.asyncio
async def test_delivery_error_closes_in_owner_and_preserves_failure_without_retry(monkeypatch):
    c = owned_transport(monkeypatch, fail_call=True)
    await c.target.manifest()
    request = {'jsonrpc':'2.0','id':'owned','method':'tools/call','params':{'name':'echo','arguments':{}}}
    with pytest.raises(ValueError, match='owned original call failure'):
        await asyncio.create_task(c.target.invoke(request,'same-owned-effect'))
    assert c.opened == c.closed and len(c.calls) == 1
    assert c.calls[0][1] == {'meta':{'atellagent/idempotencyKey':'same-owned-effect'}}


@pytest.mark.asyncio
async def test_cancelled_discovery_unwinds_in_its_owner(monkeypatch):
    c = owned_transport(monkeypatch, pause_discovery=True)
    task = asyncio.create_task(c.target.manifest())
    try:
        await asyncio.wait_for(c.entered.wait(),1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task,1)
        assert c.opened == c.closed and c.target._client is None
    finally:
        c.release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        await c.target.close()


@pytest.mark.asyncio
async def test_close_during_discovery_finishes_the_original_connection_before_return(monkeypatch):
    c = owned_transport(monkeypatch, pause_discovery=True)
    manifest = asyncio.create_task(c.target.manifest())
    closing = None
    try:
        await asyncio.wait_for(c.entered.wait(),1)
        closing = asyncio.create_task(c.target.close())
        await asyncio.sleep(.02)
        assert not closing.done(), 'Close returned while discovery could still publish a live connection'
        c.release.set()
        await asyncio.wait_for(asyncio.gather(manifest,closing),1)
        assert c.opened == c.closed and c.target._client is None
    finally:
        c.release.set()
        if not manifest.done(): manifest.cancel()
        if closing is not None and not closing.done(): closing.cancel()
        await asyncio.gather(manifest,*([closing] if closing is not None else []),return_exceptions=True)
        await c.target.close()


@pytest.mark.asyncio
async def test_concurrent_discovery_uses_only_one_connection_owner(monkeypatch):
    c = owned_transport(monkeypatch)
    assert await asyncio.gather(c.target.manifest(),c.target.manifest()) == [{'tools':[]},{'tools':[]}]
    await c.target.close()
    assert len(c.opened) == 1 and c.opened == c.closed


@pytest.mark.asyncio
async def test_cancelled_close_waits_for_owned_cleanup_before_propagating(monkeypatch):
    c = owned_transport(monkeypatch,pause_close=True)
    await c.target.manifest()
    closing = asyncio.create_task(c.target.close())
    try:
        await asyncio.wait_for(c.closing.wait(),1)
        closing.cancel()
        await asyncio.sleep(.02)
        assert not closing.done(), 'Cancelled close abandoned its connection cleanup'
        c.finish.set()
        with pytest.raises(asyncio.CancelledError): await closing
        assert c.opened == c.closed and c.target._client is None
    finally:
        c.finish.set()
        if not closing.done(): closing.cancel()
        await asyncio.gather(closing,return_exceptions=True)
        await c.target.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_call', [False, True])
async def test_real_stdio_cross_task_cleanup_reaps_owned_target(tmp_path, fail_call):
    import json
    import os
    import sys
    from pathlib import Path
    from mcp.shared.exceptions import MCPError

    reference = Path(__file__).parent / 'fixtures' / 'modern_mcp_reference_server.py'
    server = tmp_path / 'owned_reference.py'
    pid_file = tmp_path / 'owned_pid.txt'
    calls_file = tmp_path / 'owned_calls.jsonl'
    source = reference.read_text().replace(
        'import sys\n',
        'import sys\nimport os\nfrom pathlib import Path\n'
        'Path(sys.argv[1]).write_text(str(os.getpid()))\n'
    ).replace(
        '    elif method == "tools/call":\n',
        '    elif method == "tools/call":\n'
        '        with Path(sys.argv[2]).open("a") as log:\n'
        '            log.write(json.dumps(request) + "\\n")\n'
        '        if request["params"]["name"] == "fail":\n'
        '            print(json.dumps({"jsonrpc":"2.0", "id":request["id"], '
        '"error":{"code":-32603,"message":"owned synthetic call failure"}}), flush=True)\n'
        '            continue\n'
    )
    server.write_text(source)
    target = LocalMCPClient(BridgeDeploymentConfig(
        target_transport='stdio', target_command=sys.executable,
        target_args=['-u', str(server), str(pid_file), str(calls_file)]))
    try:
        assert (await target.manifest())['tools'][0]['name'] == 'echo'
        owner = target._owner_task
        request = {'jsonrpc':'2.0', 'id':'owned-native', 'method':'tools/call',
                   'params':{'name':'fail' if fail_call else 'echo', 'arguments':{}}}
        if fail_call:
            with pytest.raises(MCPError, match='owned synthetic call failure'):
                await asyncio.create_task(target.invoke(request, 'owned-native-effect'))
        else:
            response = await asyncio.create_task(target.invoke(request, 'owned-native-effect'))
            assert response['result']['content'][0]['text'] == 'ok'
        await asyncio.create_task(target.close())
        assert owner.done() and target._owner_task is None and target._client is None
        calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
        assert len(calls) == 1
        assert calls[0]['params']['_meta']['atellagent/idempotencyKey'] == 'owned-native-effect'
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
    finally:
        await target.close()


@pytest.mark.asyncio
async def test_completed_close_allows_a_fresh_connection(monkeypatch):
    c = owned_transport(monkeypatch)
    await c.target.manifest()
    await c.target.close()
    await c.target.manifest()
    await c.target.close()
    assert len(c.opened) == 2 and c.opened == c.closed


@pytest.mark.asyncio
async def test_original_call_failure_survives_a_cleanup_failure(monkeypatch):
    c = owned_transport(monkeypatch, fail_call=True)
    await c.target.manifest()
    async def failed_close():
        raise RuntimeError('owned cleanup failure')
    original_close = c.target.close
    monkeypatch.setattr(c.target, 'close', failed_close)
    request = {'jsonrpc':'2.0', 'id':'owned', 'method':'tools/call',
               'params':{'name':'echo', 'arguments':{}}}
    try:
        with pytest.raises(ValueError, match='owned original call failure') as failure:
            await c.target.invoke(request, 'owned-effect')
        assert 'owned cleanup failure' in failure.value.__notes__[0]
        assert len(c.calls) == 1
    finally:
        await original_close()


@pytest.mark.asyncio
async def test_real_stdio_cancelled_discovery_reaps_owned_target(tmp_path):
    import os
    import sys
    from pathlib import Path

    reference = Path(__file__).parent / 'fixtures' / 'modern_mcp_reference_server.py'
    server = tmp_path / 'owned_waiting_reference.py'
    pid_file = tmp_path / 'owned_pid.txt'
    waiting = tmp_path / 'owned_discovery.txt'
    source = reference.read_text().replace(
        'import sys\n',
        'import sys\nimport os\nimport time\nfrom pathlib import Path\n'
        'Path(sys.argv[1]).write_text(str(os.getpid()))\n'
        'discoveries = 0\n'
    ).replace(
        '    if method == "server/discover":\n',
        '    if method == "server/discover":\n'
        '        discoveries += 1\n'
        '        if discoveries == 1:\n'
        '            Path(sys.argv[2]).write_text("waiting")\n'
        '            while True: time.sleep(.01)\n'
    )
    server.write_text(source)
    target = LocalMCPClient(BridgeDeploymentConfig(
        target_transport='stdio', target_command=sys.executable,
        target_args=['-u', str(server), str(pid_file), str(waiting)]))
    manifest = asyncio.create_task(target.manifest())
    try:
        async with asyncio.timeout(2):
            while not waiting.exists():
                await asyncio.sleep(.01)
        owner = target._owner_task
        manifest.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(manifest, 5)
        assert owner.done() and target._owner_task is None and target._client is None
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
    finally:
        if not manifest.done():
            manifest.cancel()
        await asyncio.gather(manifest, return_exceptions=True)
        await target.close()
