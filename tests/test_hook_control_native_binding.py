"""The real governance transport carries exact device/delegation evidence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from atellagent_client.integrations.agents.hook_control import HookControlRuntime
from hook_control_support import _config, _native_binding, _Participant
from native_runtime_binding_support import runtime_case


@pytest.mark.asyncio
async def test_runtime_wires_real_governance_and_participant_to_same_binding(tmp_path):
    config = _config()
    binding = runtime_case(tmp_path).binding
    config.tenant_id, config.service_account_id, config.integration_id = (
        str(binding.device.tenant_id), str(binding.service_account_id), str(binding.integration_id))
    config.gateway_url = binding.gateway_origin
    config.cert_path, config.key_path = str(binding.stored.certificate_path), str(binding.stored.private_key_path)
    participant = _Participant(config)
    request = AsyncMock(return_value=object())
    participant.session = SimpleNamespace(request_authenticated=request)
    runtime = HookControlRuntime(config, socket_path="/private/tmp/native-binding-test.sock",
                                 native_binding=binding, participant=participant)
    runtime._bind_native_participant()
    target = config.gateway_url + "/v1/agents/boundary/actions/preflight"
    body = {"tool": "read"}
    await runtime.governance._request_gateway_async("POST", target, workflow_context={}, json=body)
    headers = request.call_args.kwargs["headers"]
    assert headers["X-Atellagent-Local-Authorization-Id"] == str(binding.authorization_id)
    assert headers["X-Atellagent-Device-Id"] == str(binding.device.device_id)
    assert "X-Atellagent-Local-User-Authorization" not in headers
    registration = participant.device_proof_provider("POST", target, body)
    assert registration["X-Atellagent-Local-Authorization-Id"] == headers["X-Atellagent-Local-Authorization-Id"]
    assert registration["X-Atellagent-Device-Nonce"] != headers["X-Atellagent-Device-Nonce"]
    assert participant.device_id == str(binding.device.device_id)


@pytest.mark.parametrize("change", ["tenant", "account", "integration", "gateway", "key"])
def test_runtime_rejects_mixed_configuration_and_activated_identity(change):
    config = _config()
    binding = _native_binding(config)
    fields = {"tenant": "tenant_id", "account": "service_account_id", "integration": "integration_id",
              "gateway": "gateway_url", "key": "key_path"}
    value = "https://other.example" if change == "gateway" else "/other/key.pem" if change == "key" else str(uuid4())
    setattr(config, fields[change], value)
    with pytest.raises(ValueError, match="exact activated native binding"):
        HookControlRuntime(config, socket_path="/private/tmp/native-binding-test.sock", native_binding=binding)
