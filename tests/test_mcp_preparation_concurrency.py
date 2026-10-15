"""Actual MCP target preparation against owned storage and typed identity doubles only."""
import asyncio
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from atellagent_client.onboarding.device_mcp_context import DeviceMCPContext
from atellagent_client.onboarding.errors import OnboardingClientError, MCPExistingRequestError
from atellagent_client.onboarding.setup_keys import SetupKeys
from atellagent_client.onboarding.setup_identity import SetupIdentity, SetupMembership
from atellagent_client.onboarding.process_lease import process_lease
from device_certificate_support import certificate_case


def case(tmp_path):
    setup, receipt, _, _ = certificate_case()
    device = SimpleNamespace(tenant_id=setup.tenant_id, owner_user_id=setup.owner_user_id,
        device_id=receipt.device_id, proof_key=setup.keys.proof_key)
    (tmp_path/'installation').mkdir(mode=0o700)
    installation = SimpleNamespace(directory=tmp_path/'installation', load=lambda:(setup,receipt,'fixture'))
    context = SimpleNamespace(installation=installation, setup=setup, client=SimpleNamespace(device=device))
    first, second = DeviceMCPContext(context), DeviceMCPContext(context)
    identity = SetupIdentity(setup.owner_user_id,(SetupMembership(setup.tenant_id,'Fixture',False),))
    management = SimpleNamespace(origin=setup.platform_origin, identity=AsyncMock(return_value=identity))
    return first, second, management, identity


async def prepare(context, management, port=8787):
    return await context.prepare_manual_target(management, display_name='Owned tools', url=f'http://127.0.0.1:{port}/mcp')


@pytest.mark.asyncio
async def test_overlapping_same_target_has_only_one_retained_draft(tmp_path):
    first, second, management, identity = case(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def current():
        entered.set()
        await release.wait()
        return identity
    management.identity = current
    a = asyncio.create_task(prepare(first,management))
    await asyncio.wait_for(entered.wait(),1)
    b = asyncio.create_task(prepare(second,management))
    await asyncio.sleep(.05)
    release.set()
    results = await asyncio.wait_for(asyncio.gather(a,b,return_exceptions=True),3)
    winners=[v for v in results if not isinstance(v,BaseException)]
    assert len(winners)==1, 'Concurrent duplicate target preparation created multiple request identities'
    errors=[v for v in results if isinstance(v,BaseException)]
    assert len(errors)==1 and isinstance(errors[0],OnboardingClientError)
    assert errors[0].code=='onboarding_mcp_request_busy' and errors[0].retryable
    assert len(list(first.drafts.directory.glob('????????-????-????-????-????????????.json')))==1
    assert first.drafts.load(winners[0].proposal.id).encode()==winners[0].encode()


@pytest.mark.asyncio
async def test_serial_retry_reuses_original_identity_without_new_keys(tmp_path,monkeypatch):
    first,second,management,_=case(tmp_path)
    draft=await prepare(first,management)
    before=draft.encode()
    monkeypatch.setattr(SetupKeys,'generate',lambda:pytest.fail('Duplicate generated replacement keys'))
    with pytest.raises(MCPExistingRequestError) as error:await prepare(second,management)
    assert error.value.request_id==draft.proposal.id and first.drafts.load(draft.proposal.id).encode()==before


@pytest.mark.asyncio
async def test_cancellation_during_identity_releases_preparation_lease(tmp_path):
    first,second,management,identity=case(tmp_path)
    entered=asyncio.Event()
    async def current():entered.set();await asyncio.Future()
    management.identity=current
    task=asyncio.create_task(prepare(first,management));await asyncio.wait_for(entered.wait(),1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert not list(first.drafts.directory.glob('????????-????-????-????-????????????.json'))
    management.identity=AsyncMock(return_value=identity)
    assert (await prepare(second,management)).proposal.device_id==second.device_context.client.device.device_id


@pytest.mark.asyncio
async def test_external_lease_rejects_before_identity_or_key_effects(tmp_path):
    first,second,management,_=case(tmp_path)
    with process_lease(first.directory/'.mcp-request.lock') as held:
        assert held
        with pytest.raises(OnboardingClientError,match='onboarding_mcp_request_busy'):await prepare(second,management)
        management.identity.assert_not_awaited()
    assert not list(first.drafts.directory.glob('????????-????-????-????-????????????.json'))
    assert (await prepare(second,management)).proposal.device_id==second.device_context.client.device.device_id


@pytest.mark.asyncio
async def test_wrong_account_does_not_leave_draft_and_release_allows_retry(tmp_path):
    first,second,management,identity=case(tmp_path)
    management.identity=AsyncMock(return_value=SetupIdentity(uuid4(),identity.memberships))
    with pytest.raises(OnboardingClientError,match='account_changed'):await prepare(first,management)
    assert not list(first.drafts.directory.glob('????????-????-????-????-????????????.json'))
    management.identity=AsyncMock(return_value=identity)
    assert (await prepare(second,management)).proposal.device_id==second.device_context.client.device.device_id


@pytest.mark.asyncio
async def test_different_explicit_targets_remain_separate(tmp_path):
    first,second,management,_=case(tmp_path)
    a=await prepare(first,management,8787);b=await prepare(second,management,8788)
    assert a.proposal.id!=b.proposal.id
    assert len(list(first.drafts.directory.glob('????????-????-????-????-????????????.json')))==2


@pytest.mark.asyncio
async def test_killed_process_releases_preparation_lease(tmp_path):
    first, second, management, _ = case(tmp_path)
    marker = tmp_path / "lease-held"
    script = """from pathlib import Path
import sys,time
from atellagent_client.onboarding.process_lease import process_lease
with process_lease(Path(sys.argv[1])) as held:
    assert held
    Path(sys.argv[2]).write_text("ready")
    time.sleep(30)
"""
    child = subprocess.Popen([sys.executable, "-c", script,
        str(first.directory / ".mcp-request.lock"), str(marker)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async def ready():
            while not marker.exists():
                assert child.poll() is None, "Lease fixture exited before acquiring the lock"
                await asyncio.sleep(.01)
        await asyncio.wait_for(ready(), 5)
        with pytest.raises(OnboardingClientError, match="onboarding_mcp_request_busy"):
            await prepare(second, management)
        management.identity.assert_not_awaited()
        assert not list(first.drafts.directory.glob('????????-????-????-????-????????????.json'))
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)
    assert (await prepare(second, management)).proposal.device_id == second.device_context.client.device.device_id
