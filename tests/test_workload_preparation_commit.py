"""Actual owned workload storage at the draft/settings atomic commit boundary."""
from dataclasses import replace
import json
import pytest
from atellagent_client.onboarding.errors import OnboardingClientError, MCPExistingRequestError
from atellagent_client.onboarding.setup_keys import SetupKeys
from atellagent_client.onboarding.workload_local_inputs import WorkloadLocalInputs
from atellagent_client.onboarding.workload_setup_store import WorkloadSetupStore
from atellagent_client.onboarding.workload_local_input_store import WorkloadLocalInputStore
from test_mcp_preparation_concurrency import case as mcp_case, prepare


@pytest.mark.asyncio
async def test_interrupted_commit_reopens_exact_identity_and_initial_launch_inputs(tmp_path, monkeypatch):
    first, second, management, _ = mcp_case(tmp_path)
    import atellagent_client.onboarding.workload_setup_store as module
    original = module.create_private_file
    committed = []
    def commit_then_interrupt(path, content):
        original(path, content)
        committed.append(path)
        raise OSError('Owned interruption after durable commit')
    with monkeypatch.context() as patch:
        patch.setattr(module, 'create_private_file', commit_then_interrupt)
        with pytest.raises(OnboardingClientError, match='storage_unavailable'):
            await prepare(first, management)
    assert len(committed) == 1
    from uuid import UUID
    retained = second.drafts.load(UUID(committed[0].stem))
    before = retained.encode()
    assert second.local_inputs.load(retained).deployment['target_url'] == 'http://127.0.0.1:8787/mcp'
    with monkeypatch.context() as patch:
        patch.setattr(SetupKeys, 'generate', lambda: pytest.fail('Recovered identity generated new keys'))
        with pytest.raises(MCPExistingRequestError) as error:
            await prepare(second, management)
    assert error.value.request_id == retained.proposal.id
    assert second.drafts.load(retained.proposal.id).encode() == before
    assert (await prepare(second, management, 8788)).proposal.id != retained.proposal.id


@pytest.mark.asyncio
async def test_failure_before_commit_leaves_no_visible_identity_or_settings(tmp_path, monkeypatch):
    first, second, management, _ = mcp_case(tmp_path)
    def unavailable(*_args):
        raise OSError('Owned failure before commit')
    with monkeypatch.context() as patch:
        patch.setattr('atellagent_client.onboarding.workload_setup_store.create_private_file', unavailable)
        with pytest.raises(OnboardingClientError, match='storage_unavailable'):
            await prepare(first, management)
    assert not list(first.drafts.directory.glob('*.json'))
    assert (await prepare(second, management)).proposal.id is not None


@pytest.mark.asyncio
async def test_settings_update_is_atomic_and_identity_is_unchanged(tmp_path, monkeypatch):
    first, second, management, _ = mcp_case(tmp_path)
    draft = await prepare(first, management)
    before = draft.encode()
    inputs = first.local_inputs.load(draft)
    changed = replace(inputs, target_idempotent=True)
    first.local_inputs.save(draft=draft, inputs=changed)
    assert second.local_inputs.load(draft) == changed
    assert second.drafts.load(draft.proposal.id).encode() == before
    def unavailable(*_args, **_kwargs): raise OSError('Owned interrupted replacement')
    with monkeypatch.context() as patch:
        patch.setattr('atellagent_client.onboarding.workload_local_input_store.replace_private_record', unavailable)
        with pytest.raises(OnboardingClientError):
            first.local_inputs.save(draft=draft, inputs=inputs)
    assert second.local_inputs.load(draft) == changed
    assert second.drafts.load(draft.proposal.id).encode() == before


@pytest.mark.asyncio
async def test_legacy_draft_and_settings_remain_readable_and_mutable(tmp_path):
    first, _, management, _ = mcp_case(tmp_path)
    draft = await prepare(first, management)
    inputs = first.local_inputs.load(draft)
    legacy = WorkloadSetupStore(tmp_path / 'legacy')
    legacy.save_initial(draft)
    settings = WorkloadLocalInputStore(legacy)
    with pytest.raises(OnboardingClientError, match='local_inputs_required'):
        settings.load(draft)
    settings.save(draft=draft, inputs=inputs)
    changed = replace(inputs, target_idempotent=True)
    settings.save(draft=draft, inputs=changed)
    assert settings.load(draft) == changed and legacy.load(draft.proposal.id).encode() == draft.encode()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['revision', 'extra', 'inputs', 'identity', 'size'])
async def test_corrupt_prepared_record_never_falls_back_or_overwrites(tmp_path, change):
    first, _, management, _ = mcp_case(tmp_path)
    draft = await prepare(first, management)
    inputs = first.local_inputs.load(draft)
    path = first.drafts._path(draft.proposal.id)
    record = json.loads(path.read_bytes())
    assert record['revision'] == 2, 'Initial draft and settings still use separate commits'
    if change == 'revision': record['revision'] = True
    elif change == 'extra': record['extra'] = 'unknown'
    elif change == 'inputs': record['inputs']['deployment']['client_id'] = 'foreign'
    elif change == 'identity': record['draft']['proposal']['id'] = '00000000-0000-0000-0000-000000000001'
    else: record['inputs']['handler'] = 'x' * 70000
    raw = json.dumps(record).encode();path.write_bytes(raw)
    with pytest.raises(OnboardingClientError): first.local_inputs.load(draft)
    with pytest.raises(OnboardingClientError): first.local_inputs.save(draft=draft, inputs=inputs)
    assert path.read_bytes() == raw


from atellagent_client.onboarding.workload_context import WorkloadContext
from test_workload_setup_flow import flow_case
from test_workload_credential_store import stored_case
from test_workload_proof_client import case
from test_workload_management import draft, receipt


@pytest.mark.asyncio
async def test_paired_record_approved_runtime_preserves_receipt_keys_and_updated_settings(flow_case, tmp_path):
    c = flow_case
    context = WorkloadContext(tmp_path / 'paired-runtime')
    draft = c.args['draft']
    original = draft.encode()
    inputs = WorkloadLocalInputs({'type': 'sdk'}, handler='customer.model:Handler', target_idempotent=True)
    context.drafts.save_prepared(draft=draft, inputs=inputs)
    context.receipts.bind(draft=draft, receipt=c.args['receipt'])
    context.credentials.save(**{**c.args, 'draft_store': context.drafts}, credential=c.credential)
    c.state.status = 'issued'
    config, launch = await context.runtime(draft.proposal.id)
    assert config.integration_id == str(c.credential.integration_id) and launch == inputs
    changed = replace(inputs, handler='customer.model:NewHandler')
    context.local_inputs.save(draft=draft, inputs=changed)
    reopened = WorkloadContext(context.directory)
    second, launch = await reopened.runtime(draft.proposal.id)
    assert second.cert_path == config.cert_path and launch == changed
    assert reopened.drafts.load(draft.proposal.id).encode() == original
    assert reopened.receipts.load(draft) == c.args['receipt']
    assert not any(path.endswith('/claim') or path.endswith('/certificate') for path in c.state.calls)


@pytest.mark.asyncio
async def test_paired_record_submission_uses_exact_proof_and_excludes_local_settings(tmp_path, draft, monkeypatch):
    import base64
    from hashlib import sha256
    import httpx
    from atellagent_client.onboarding.setup_management import SetupManagementClient
    from atellagent_client.onboarding.device_login_models import ManagementTokens
    from atellagent_client.onboarding.proofs import canonical_json
    store = WorkloadSetupStore(tmp_path / 'paired-submission')
    inputs = WorkloadLocalInputs({'type': 'sdk'}, handler='customer.model:Handler', target_idempotent=True)
    store.save_prepared(draft=draft, inputs=inputs)
    original = draft.encode()
    calls = []
    def handler(request):
        calls.append(request.method)
        assert request.headers['Authorization'] == 'Bearer fixture-access'
        if request.method == 'GET':
            return httpx.Response(200, json={'user_id': str(draft.setup.owner_user_id), 'selection_required': True,
                'memberships': [{'tenant_id': str(draft.setup.tenant_id), 'tenant_name': 'Fixture', 'can_approve': True}]})
        payload = json.loads(request.content)
        assert set(payload) == {'tenant_id', 'proposal', 'proof', 'device_proof'}
        assert payload['proposal'] == draft.proposal.payload() and payload['device_proof'] is None
        assert 'customer.model:Handler' not in request.content.decode() and 'PRIVATE KEY' not in request.content.decode()
        body = {'tenant_id': payload['tenant_id'], 'setup_operator_id': str(draft.setup.owner_user_id), 'proposal': payload['proposal']}
        proof = payload['proof']
        material = '\n'.join(('atellagent-onboarding-request-v1', str(draft.setup.operation_id), 'POST',
            request.url.path, sha256(canonical_json(body)).hexdigest(), proof['timestamp'], proof['nonce'])).encode()
        draft.setup.keys.proof_key.public_key().verify(base64.urlsafe_b64decode(proof['signature'] + '=='), material)
        return httpx.Response(200, json=receipt(draft))
    factory = httpx.AsyncClient
    monkeypatch.setattr('atellagent_client.onboarding.setup_management.httpx.AsyncClient',
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(handler)))
    async with SetupManagementClient(origin=draft.setup.platform_origin, tokens=ManagementTokens(300, 'fixture-access', None)) as management:
        assert (await management.submit_workload(draft=draft, store=store)).status == 'pending'
    assert calls == ['GET', 'POST'] and store.load(draft.proposal.id).encode() == original


def test_oversized_prepared_inputs_leave_no_committed_identity(tmp_path, draft):
    store = WorkloadSetupStore(tmp_path / 'oversized-preparation')
    inputs = WorkloadLocalInputs({'type': 'sdk'}, handler='x' * 70000 + ':Handler')
    with pytest.raises(OnboardingClientError, match='storage_unavailable'):
        store.save_prepared(draft=draft, inputs=inputs)
    assert not list(store.directory.glob('*.json'))
