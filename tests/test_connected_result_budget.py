"""Uncommittable handler results produce a bounded, schema-valid terminal error."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from connected_runtime_support import _config, _message
from atellagent_client.connected.participant import ConnectedParticipant
from atellagent_client.connected.contracts import ConnectedHandlerResult, parse_connected_message

MAXIMUM = 1024 * 1024


def owned_commit(monkeypatch):
    parsed, wire_sizes, calls = [], [], []

    message_id = None

    async def request(method, url, **kwargs):
        calls.append(url)
        wire = httpx.Request(method, url, json=kwargs['json'])
        wire_sizes.append(len(wire.content))
        if len(wire.content) > MAXIMUM:
            status, body = 413, {"detail": "owned HTTP size rejection"}
        elif any(len(json.dumps(kwargs['json'][field], separators=(',', ':'), sort_keys=True).encode()) > MAXIMUM
                 for field in ['result_payload', 'evidence_payload']):
            status, body = 500, {"detail": "owned encryption size rejection"}
        else:
            parsed.append(kwargs['json'])
            status, body = 200, {"message_id": message_id, "result_id": str(uuid4()),
                "terminal_status": kwargs['json']['terminal_status'], "committed_at": "2026-10-11T00:00:00Z"}
        return httpx.Response(status, json=body, request=wire, extensions={"http_version": b"HTTP/2"})

    session = SimpleNamespace(base_url='https://owned.invalid', request_authenticated=request)
    monkeypatch.setattr('atellagent_client.connected.participant.ConnectedCapabilityValidator', lambda *_:object())
    participant = ConnectedParticipant(_config(), session=session)
    participant._instance_id = str(uuid4())
    # Hold renewal until the handler completes; no network/real lease is created.
    async def renewal(*_):
        await asyncio.Event().wait()
    monkeypatch.setattr(participant, '_renew_lease', renewal)

    def make_message(operation):
        nonlocal message_id
        envelope = _message()['message']
        envelope['operation'] = operation
        message = parse_connected_message(envelope)
        message_id = message.message_id
        return message
    return SimpleNamespace(p=participant, parsed=parsed, wire_sizes=wire_sizes, calls=calls, message=make_message)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,operation', [
    ('wire','agent.process'), ('evidence','agent.process'), ('unicode','agent.process'),
    ('large','agent.process'), ('large','workflow.execute'),
    ('large','workflow.resume'), ('large','workflow.cancel')])
async def test_uncommittable_result_commits_one_small_terminal_error(monkeypatch, kind, operation):
    c = owned_commit(monkeypatch)
    payload = {'text': 'x' * (MAXIMUM - 20)} if kind == 'wire' else {'text':'x' * (MAXIMUM + 1)}
    evidence = {}
    if kind == 'unicode':
        payload = {'text':'\U0001f642' * 100_000}
    if kind == 'evidence':
        evidence, payload = payload, {}
    handled = []
    async def handler(delivery, _actions):
        handled.append(delivery.idempotency_key)
        return ConnectedHandlerResult.succeeded(result_schema='owned-original-result',
            result_payload=payload, evidence_payload=evidence)
    await c.p._process(c.message(operation), handler)
    assert handled == ['effect-1'] and len(c.calls) == 1 and len(c.parsed) == 1
    terminal = c.parsed[0]
    assert terminal['terminal_status'] == 'failed' and c.wire_sizes[0] < 2048
    assert terminal['evidence_payload'] == {}
    if operation.startswith('workflow.'):
        assert terminal['result_schema'] == 'atellagent.connected.workflow-outcome.v1'
        assert terminal['result_payload']['protocol_version'] == 'v1'
        assert terminal['result_payload']['outcome'] == 'failed'
        assert terminal['result_payload']['error']['code'] == 'connected_result_too_large'
    else:
        assert terminal['result_schema'] == 'atellagent.connected.error.v1'
        assert terminal['result_payload']['error_type'] == 'ConnectedResultTooLarge'


@pytest.mark.asyncio
async def test_ordinary_result_keeps_schema_payload_evidence_and_one_call(monkeypatch):
    c = owned_commit(monkeypatch)
    value = ConnectedHandlerResult.succeeded(result_schema='owned-result',
        result_payload={'text':'normal \U0001f642'}, evidence_payload={'owned':True})
    await c.p._commit_result(c.message('agent.process'), value)
    assert len(c.calls) == 1 and len(c.parsed) == 1
    assert c.parsed[0]['terminal_status'] == 'succeeded'
    assert c.parsed[0]['result_schema'] == value.result_schema
    assert c.parsed[0]['result_payload'] == value.result_payload
    assert c.parsed[0]['evidence_payload'] == value.evidence_payload


@pytest.mark.asyncio
@pytest.mark.parametrize('over', [0, 1])
async def test_wire_budget_exact_boundary_keeps_success_only_when_it_fits(monkeypatch, over):
    c = owned_commit(monkeypatch)
    message = c.message('agent.process')
    result = ConnectedHandlerResult.succeeded(result_schema='owned-result', result_payload={'text':''})
    body = {'lease_id':message.lease.lease_id, 'lease_token':message.lease.lease_token,
        'terminal_status':result.terminal_status, 'result_schema':result.result_schema,
        'result_payload':result.result_payload, 'evidence_payload':result.evidence_payload}
    overhead = len(httpx.Request('POST','https://owned.invalid',json=body).content)
    result.result_payload['text'] = 'x' * (MAXIMUM - overhead + over)
    await c.p._commit_result(message, result)
    assert len(c.calls) == 1
    assert c.parsed[0]['terminal_status'] == ('failed' if over else 'succeeded')
    assert c.wire_sizes[0] < 2048 if over else c.wire_sizes[0] == MAXIMUM


@pytest.mark.asyncio
@pytest.mark.parametrize('over', [0, 1])
async def test_encrypted_unicode_field_exact_boundary(monkeypatch, over):
    c = owned_commit(monkeypatch)
    overhead = len(json.dumps({'text':''},separators=(',',':'),sort_keys=True).encode())
    repeats, tail = divmod(MAXIMUM - overhead + over, 12)
    value = {'text':'\U0001f642' * repeats + 'x' * tail}
    assert len(json.dumps(value,separators=(',',':'),sort_keys=True).encode()) == MAXIMUM + over
    await c.p._commit_result(c.message('agent.process'),
        ConnectedHandlerResult.succeeded(result_schema='owned-result',result_payload=value))
    assert len(c.calls) == 1
    assert c.parsed[0]['terminal_status'] == ('failed' if over else 'succeeded')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['nan', 'cycle', 'object', 'surrogate'])
async def test_invalid_json_result_commits_a_valid_error_instead_of_replaying(monkeypatch, kind):
    c = owned_commit(monkeypatch)
    value = {'value':float('nan')}
    if kind == 'cycle':
        value['value'] = value
    elif kind == 'object':
        value['value'] = object()
    elif kind == 'surrogate':
        value['value'] = '\ud800'
    await c.p._commit_result(c.message('workflow.resume'),
        ConnectedHandlerResult.succeeded(result_schema='owned-result',result_payload=value))
    assert len(c.calls) == 1 and c.wire_sizes[0] < 2048
    assert c.parsed[0]['result_payload']['outcome'] == 'failed'
    assert c.parsed[0]['result_payload']['error']['code'] == 'connected_result_invalid'
