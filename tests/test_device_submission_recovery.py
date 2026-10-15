"""Retained request interruption tests: actual client code, private fixtures and mock HTTP only."""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from test_pending_device_setup import prepared
from atellagent_client.onboarding import pending_device_resume, setup_overview
from atellagent_client.onboarding.attended_device import DeviceSetup
from atellagent_client.onboarding.device_login_models import ManagementTokens
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.setup_keys import SetupKeys
from atellagent_client.cli import setup_device


def mock_status(monkeypatch, pending, draft, receipt, *, missing=True, owner=None):
    state = {'missing': missing, 'requests': [], 'submits': 0}
    factory = httpx.AsyncClient
    def handler(request):
        state['requests'].append((request.method, request.url.path))
        if request.url.path.endswith('/identity'):
            return httpx.Response(200, json={'user_id': str(owner or draft.owner_user_id), 'selection_required': True,
                'memberships': [{'tenant_id': str(draft.tenant_id), 'tenant_name': 'Fixture', 'can_approve': False}]})
        if request.url.path == '/api/client-onboarding/sessions':
            body = json.loads(request.content)
            assert request.headers['authorization'] == 'Bearer fixture-token'
            assert body['proposal'] == draft.proposal() and body['tenant_id'] == str(draft.tenant_id)
            state['submits'] += 1
            state['missing'] = False
            return httpx.Response(200, json={'id': str(receipt.id), 'kind': receipt.kind, 'status': 'pending',
                'device_id': str(receipt.device_id), 'revision': 1,
                'expires_at': receipt.expires_at.isoformat(), 'claim_expires_at': None})
        assert request.url.path == f'/api/client-onboarding/sessions/{draft.operation_id}'
        assert 'authorization' not in request.headers
        assert request.headers['x-atellagent-onboarding-signature']
        if state['missing']:
            return httpx.Response(403, json={'error': {'code': 'onboarding_permission_denied'}})
        return httpx.Response(200, json={'id': str(receipt.id), 'kind': receipt.kind, 'status': 'pending',
            'device_id': str(receipt.device_id), 'expires_at': receipt.expires_at.isoformat(),
            'claim_expires_at': None, 'device_issuance': None})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: factory(**kw, transport=httpx.MockTransport(handler)))
    return state


@pytest.mark.asyncio
async def test_saved_before_submission_has_a_visible_exact_recovery_action(tmp_path, monkeypatch):
    pending, draft, receipt = prepared(tmp_path)
    before = pending.path.read_bytes(), draft.encode()
    state = mock_status(monkeypatch, pending, draft, receipt)
    overview = await setup_overview.overview(pending.installation.directory)
    row = overview['pending']
    assert row['status'] == 'submission_unconfirmed' and row['device_id'] is None
    assert row['setup_id'] == str(draft.operation_id) and row['tenant_id'] == str(draft.tenant_id)
    assert overview['device'] is None and overview['assignments'] == []
    assert row['connection_submission_complete'] is False and row['requested_connections'] == []
    assert 'PRIVATE KEY' not in json.dumps(overview)
    assert (pending.path.read_bytes(), pending.load()[0].encode()) == before and state['submits'] == 0


@pytest.mark.asyncio
async def test_resume_requests_authentication_for_an_unconfirmed_draft(tmp_path, monkeypatch):
    pending, draft, receipt = prepared(tmp_path)
    mock_status(monkeypatch, pending, draft, receipt)
    with pytest.raises(OnboardingClientError, match='onboarding_setup_submission_unconfirmed'):
        await pending_device_resume.resume_pending_device_setup(pending)
    assert pending.load()[1] is None and pending.load()[0].encode() == draft.encode()


@pytest.mark.asyncio
@pytest.mark.parametrize('desktop', [False, True])
async def test_explicit_continue_resubmits_exact_saved_intent_after_fresh_login(tmp_path, monkeypatch, desktop):
    pending, draft, receipt = prepared(tmp_path)
    state = mock_status(monkeypatch, pending, draft, receipt)
    monkeypatch.setattr(setup_device, 'installation_directory', lambda: pending.installation.directory)
    login = AsyncMock(return_value=ManagementTokens.from_payload(dict(access_token='fixture-token', refresh_token=None, expires_in=60, token_type='Bearer')))
    monkeypatch.setattr(setup_device, 'login', login)
    monkeypatch.setattr(SetupKeys, 'generate', lambda: pytest.fail('Recovery generated replacement keys'))
    async def wait(flow, seconds, **kwargs):
        assert flow.draft.encode() == draft.encode() and seconds == 12
        assert (await flow.advance()).stage == 'awaiting_approval'
    monkeypatch.setattr(setup_device, 'wait_for_setup', wait)
    interaction = SimpleNamespace(emit=lambda *args, **kwargs: None) if desktop else None
    await setup_device.run(['resume', '--wait-seconds', '12'], interaction=interaction)
    login.assert_awaited_once()
    assert login.call_args.args == (draft.platform_origin,)
    if desktop: assert login.call_args.kwargs['interaction'] is interaction
    assert state['submits'] == 1 and pending.load()[0].encode() == draft.encode()
    assert pending.load()[1].device_id == receipt.device_id


@pytest.mark.asyncio
async def test_wrong_signed_in_account_cannot_relabel_or_submit_retained_request(tmp_path, monkeypatch):
    pending, draft, receipt = prepared(tmp_path)
    state = mock_status(monkeypatch, pending, draft, receipt, owner=uuid4())
    monkeypatch.setattr(setup_device, 'installation_directory', lambda: pending.installation.directory)
    monkeypatch.setattr(setup_device, 'login', AsyncMock(return_value=ManagementTokens.from_payload(dict(access_token='fixture-token', expires_in=60, token_type='Bearer'))))
    with pytest.raises(SystemExit):
        await setup_device.run(['resume'])
    assert state['submits'] == 0 and pending.load()[0].encode() == draft.encode() and pending.load()[1] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('bound', [False, True])
@pytest.mark.parametrize('status', [401, 403, 404, 500])
async def test_other_failures_and_bound_receipts_do_not_trigger_resubmission(tmp_path, monkeypatch, bound, status):
    pending, draft, receipt = prepared(tmp_path)
    if bound: pending.bind_receipt(receipt)
    class Status:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def status(self): raise OnboardingClientError('onboarding_permission_denied', status=status)
    monkeypatch.setattr(pending_device_resume, 'SetupStatusClient', Status)
    monkeypatch.setattr(setup_overview, 'SetupStatusClient', Status)
    if not bound and status == 403:
        with pytest.raises(OnboardingClientError, match='submission_unconfirmed'):
            await pending_device_resume.resume_pending_device_setup(pending)
        assert (await setup_overview.overview(pending.installation.directory))['pending']['status'] == 'submission_unconfirmed'
    else:
        # Bound receipt resumes its known flow and never re-authenticates/re-submits.
        if bound: assert (await pending_device_resume.resume_pending_device_setup(pending)).receipt.device_id == receipt.device_id
        else:
            with pytest.raises(OnboardingClientError, match='permission_denied'):
                await pending_device_resume.resume_pending_device_setup(pending)
        with pytest.raises(OnboardingClientError, match='permission_denied'):
            await setup_overview.overview(pending.installation.directory)


@pytest.mark.asyncio
async def test_lost_ack_recovers_by_proof_without_login(tmp_path, monkeypatch):
    pending, draft, receipt = prepared(tmp_path)
    state = mock_status(monkeypatch, pending, draft, receipt, missing=False)
    monkeypatch.setattr(setup_device, 'installation_directory', lambda: pending.installation.directory)
    login = AsyncMock(side_effect=AssertionError('Proof recovery must not initiate sign-in'))
    monkeypatch.setattr(setup_device, 'login', login)
    monkeypatch.setattr(setup_device, 'wait_for_setup', AsyncMock())
    await setup_device.run(['resume'])
    assert pending.load()[1].device_id == receipt.device_id and state['submits'] == 0
    login.assert_not_awaited()
