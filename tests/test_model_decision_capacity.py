"""Fail-closed overload classification and content-free request diagnostics."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest

from atellagent_client.integrations.agents.control_gateway_errors import ControlGatewayErrorMixin
from atellagent_client.integrations.agents.control_model_decisions import model_decision_async, model_decision_sync
from atellagent_client.integrations.agents.hook_control_helpers import (
    _request_correlation, _safe_error_code, _safe_http_status, _safe_request_id,
)
from atellagent_client.integrations.agents.host_hook_responses import _failure
from atellagent_client.protocol.agent_contracts import ModelDecisionRequest
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError


def governance(status, payload):
    response = httpx.Response(status, json=payload)
    return SimpleNamespace(
        identity_mode="boundary_identity_only",
        _merge_context=lambda **kwargs: {},
        gateway_session=SimpleNamespace(base_url="https://fixture.invalid"),
        config=SimpleNamespace(api_version="v1"),
        _request_gateway_sync=Mock(return_value=response),
        _request_gateway_async=AsyncMock(return_value=response),
        _raise_gateway_error=lambda code, body: ControlGatewayErrorMixin._raise_gateway_error(None, code, body),
    )


@pytest.mark.parametrize("synchronous", [True, False])
@pytest.mark.parametrize("status,detail,code", [
    (503, {"error_kind": "model_input_capacity_exhausted", "private": "secret-response"},
     "model_decision_capacity_exhausted"),
    (502, {"error_kind": "model_input_capacity_exhausted"}, "model_decision_gateway_server_status"),
    (503, {"error_kind": "arbitrary-private-code"}, "model_decision_gateway_server_status"),
])
def test_overload_keeps_safe_status_and_exact_request_id_without_body_or_retry(synchronous, status, detail, code):
    control = governance(status, {"detail": detail})
    request = ModelDecisionRequest(input_scope="turn_entry", messages=[{"role": "user", "content": "secret-prompt"}])
    with pytest.raises(PolicyTransportError) as caught:
        if synchronous:
            model_decision_sync(control, request=request)
        else:
            asyncio.run(model_decision_async(control, request=request))
    transport = control._request_gateway_sync if synchronous else control._request_gateway_async
    assert transport.call_count == 1
    request_headers = transport.call_args.kwargs["request_headers"]
    request_id = request_headers["X-Model-Decision-Request-ID"]
    assert str(UUID(request_id)) == request_id == _safe_request_id(caught.value)
    assert request_headers["X-Atellagent-Action-Key"] == f"model-decision:{request.request_fingerprint}"
    assert _safe_error_code(caught.value) == code
    assert _safe_http_status(caught.value) == status
    assert "secret" not in str(caught.value) and "arbitrary-private-code" not in str(caught.value)
    # The socket normally wraps this transport error as HookControlError.
    from atellagent_client.integrations.agents.hook_control_protocol import HookControlError
    host = _failure(HookControlError(code))
    assert host.exit_code == 2
    if code == "model_decision_capacity_exhausted":
        assert "inspection service is busy" in host.stderr


@pytest.mark.parametrize("synchronous", [True, False])
def test_authored_policy_denial_is_not_reclassified_as_busy(synchronous):
    control = governance(403, {"error": {"violation_type": "test_policy_denied"}})
    request = ModelDecisionRequest(input_scope="turn_entry", messages=[{"role": "user", "content": "fixture"}])
    with pytest.raises(PolicyViolationError) as caught:
        if synchronous:
            model_decision_sync(control, request=request)
        else:
            asyncio.run(model_decision_async(control, request=request))
    assert caught.value.violation_type == "test_policy_denied"


def test_prompt_correlation_omits_prompt_text_but_distinguishes_turns():
    params = {"host": "codex", "session_id": "private-session", "turn_id": "private-turn", "messages": []}
    correlation = _request_correlation(params)
    assert len(correlation) == 24 and "private" not in correlation
    assert _request_correlation(params | {"messages": [{"content": "secret-prompt"}]}) == correlation
    assert _request_correlation(params | {"turn_id": "another-turn"}) != correlation
    assert _safe_request_id(SimpleNamespace(request_id="secret\nforged")) == "unavailable"
