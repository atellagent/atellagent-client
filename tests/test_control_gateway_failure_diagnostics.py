"""Response correlation is log-only and cannot change a refusal or retain content."""
import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from atellagent_client.integrations.agents import control_actions as actions
from atellagent_client.integrations.agents.control_gateway_errors import (
    ControlGatewayErrorMixin, CONTROL_GATEWAY_FAILURE_CODES, raise_control_response_error,
)
from atellagent_client.integrations.agents.hook_control_helpers import (
    _safe_error_code, _safe_gateway_failure_code, _safe_request_id,
)
from atellagent_client.integrations.agents.hook_control_protocol import HOOK_CONTROL_PROTOCOL
from atellagent_client.integrations.agents.hook_control_transport import HookControlTransportMixin
from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence, GovernanceCallContext, GovernanceReceipt,
)
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError

REQUEST_ID = 'b302663d-72c4-4db1-8baa-8eb57f35013c'
FAILURE = 'service_account_runtime_authority_unavailable'


def governance(status=503, payload=None, request_id=REQUEST_ID):
    response = httpx.Response(status, json=payload if payload is not None else {
        'error': FAILURE, 'message': 'private-response-content',
    }, headers={'X-Request-ID': request_id, 'X-Private': 'private-header'})
    return SimpleNamespace(
        identity_mode='boundary_identity_only', _merge_context=lambda **kw: {},
        gateway_session=SimpleNamespace(base_url='https://fixture.invalid'),
        config=SimpleNamespace(api_version='v1'),
        _request_gateway_sync=Mock(return_value=response),
        _request_gateway_async=AsyncMock(return_value=response),
        _raise_gateway_error=lambda status, payload: ControlGatewayErrorMixin._raise_gateway_error(None, status, payload),
    ), response


@pytest.mark.parametrize('operation', [
    'bootstrap_sync', 'bootstrap_async', 'preflight_sync', 'preflight_async',
    'postflight_sync', 'postflight_async', 'native_hook_outcome_async',
])
def test_each_action_response_keeps_one_request_and_its_diagnostics(operation):
    g, _ = governance()
    context = GovernanceCallContext(tool_name='fixture_read', tool_call_id='private-call',
                                    arguments={'text': 'private-tool-argument'})
    receipt = GovernanceReceipt(action_key='fixture-action', allowed=True, workflow_context={})
    args, kwargs = [g], {}
    if operation.startswith('bootstrap'): args.append(ExternalIdentityEvidence())
    elif operation.startswith('preflight'): args.append(context)
    elif operation.startswith('postflight'):
        args.append(context); kwargs.update(receipt=receipt, result_payload={'text':'private-result'}, success=True)
    else: kwargs.update(action_key='fixture-action', action_binding_fingerprint='a'*64,
                        outcome_observation='result_observed', success=True)
    with pytest.raises(PolicyTransportError) as caught:
        result = getattr(actions, operation)(*args, **kwargs)
        if operation.endswith('_async'): asyncio.run(result)
    error = caught.value
    assert _safe_error_code(error) == 'control_gateway_server_status'
    assert _safe_request_id(error) == REQUEST_ID and _safe_gateway_failure_code(error) == FAILURE
    assert not isinstance(error, PolicyViolationError)
    assert sum([g._request_gateway_sync.call_count, g._request_gateway_async.call_count]) == 1
    assert 'private' not in str(error)
    assert not any(key in vars(error) for key in ['headers','payload','response','detail'])


@pytest.mark.parametrize('wrapper', ['top', 'detail', 'error'])
@pytest.mark.parametrize('code', sorted(CONTROL_GATEWAY_FAILURE_CODES))
def test_only_known_dependency_names_survive_supported_envelopes(wrapper, code):
    body = {'code':code, 'message':'private-response-content'}
    payload = body if wrapper == 'top' else {wrapper:body}
    g, response = governance(payload=payload)
    with pytest.raises(PolicyTransportError) as caught: raise_control_response_error(g,response,payload)
    assert _safe_gateway_failure_code(caught.value) == code
    assert _safe_error_code(caught.value) == 'control_gateway_server_status'
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('code', ['private-code', 'secret\nforged', {'x':'secret'}, ['secret'], 7, None])
def test_arbitrary_or_malformed_codes_cannot_enter_logs(code):
    g,response = governance(payload={'detail':{'code':code,'error':FAILURE,'message':'secret'}})
    with pytest.raises(PolicyTransportError) as caught:raise_control_response_error(g,response,response.json())
    assert _safe_gateway_failure_code(caught.value) == 'unavailable'
    assert not hasattr(caught.value,'gateway_failure_code')


@pytest.mark.parametrize('request_id', ['private-id', 'secret\nforged', 'a'*500, REQUEST_ID+'x', ''])
def test_non_uuid_request_ids_are_not_retained(request_id):
    # A plain response-like object also covers malformed headers rejected by HTTPX.
    g,_=governance();response=SimpleNamespace(status_code=503,headers={'X-Request-ID':request_id})
    with pytest.raises(PolicyTransportError) as caught:raise_control_response_error(g,response,{'error':FAILURE})
    assert _safe_request_id(caught.value)=='unavailable' and not hasattr(caught.value,'request_id')


def test_authored_policy_remains_the_identical_refusal_without_transport_attribution():
    error=PolicyViolationError('fixture policy denial','fixture_denial',{})
    g,response=governance(403,{'detail':{'violation_type':'fixture_denial','code':FAILURE}})
    g._raise_gateway_error=Mock(side_effect=error)
    with pytest.raises(PolicyViolationError) as caught:raise_control_response_error(g,response,response.json())
    assert caught.value is error
    assert _safe_error_code(error)=='fixture_denial'
    assert _safe_request_id(error)=='unavailable' and _safe_gateway_failure_code(error)=='unavailable'


def test_malformed_diagnostic_headers_cannot_mask_the_original_transport_error():
    class BrokenHeaders:
        def get(self,*args):raise RuntimeError('private-header-error')
    error=PolicyTransportError('Remote control service is unavailable')
    error.safe_code='control_gateway_server_status';error.http_status=503
    g,_=governance();g._raise_gateway_error=Mock(side_effect=error)
    with pytest.raises(PolicyTransportError) as caught:
        raise_control_response_error(g,SimpleNamespace(status_code=503,headers=BrokenHeaders()),{'error':FAILURE})
    assert caught.value is error and _safe_request_id(error)=='unavailable'


def test_dependency_diagnostic_cannot_reclassify_a_policy_or_unknown_exception():
    for error in [PolicyViolationError('denied','fixture_denial',{}), RuntimeError('fixture')]:
        error.gateway_failure_code=FAILURE;error.safe_code='control_gateway_server_status'
        assert _safe_gateway_failure_code(error)=='unavailable'


def test_rpc_failure_logs_only_fixed_diagnostics_without_changing_host_response(caplog):
    g,response=governance()
    with pytest.raises(PolicyTransportError) as caught:raise_control_response_error(g,response,response.json())
    fake=SimpleNamespace(rpc_timeout_seconds=2,mcp_rpc_timeout_seconds=2,
                         _dispatch=AsyncMock(side_effect=caught.value),_record_event_health=Mock())
    packet=json.dumps({'protocol_version':HOOK_CONTROL_PROTOCOL,'id':'fixture-rpc','method':'action.preflight',
        'params':{'host':'codex','session_id':'private-session','turn_id':'private-turn',
                  'tool_call_id':'private-call','arguments':{'text':'private-prompt'}}}).encode()
    with caplog.at_level(logging.WARNING):result=asyncio.run(HookControlTransportMixin._response_for(fake,packet))
    assert result=={'id':'fixture-rpc','ok':False,'error':{'code':'control_gateway_server_status'}}
    assert f'request_id={REQUEST_ID}' in caplog.text and f'gateway_failure_code={FAILURE}' in caplog.text
    assert 'http_status=503' in caplog.text and 'private-' not in caplog.text
    fake._dispatch.assert_awaited_once();fake._record_event_health.assert_called_once()


@pytest.mark.parametrize('synchronous', [True, False])
def test_preparse_capacity_envelope_keeps_its_known_error_kind(synchronous):
    g, response = governance(payload={'detail': {'error_kind': 'model_input_capacity_exhausted',
                                               'retryable': True, 'message': 'private-body'}})
    context = GovernanceCallContext(tool_name='fixture_read')
    with pytest.raises(PolicyTransportError) as caught:
        if synchronous: actions.preflight_sync(g, context)
        else: asyncio.run(actions.preflight_async(g, context))
    assert _safe_gateway_failure_code(caught.value) == 'model_input_capacity_exhausted'
    assert _safe_request_id(caught.value) == REQUEST_ID
    assert _safe_error_code(caught.value) == 'control_gateway_server_status'
    assert 'private' not in str(caught.value)
