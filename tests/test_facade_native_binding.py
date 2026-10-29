"""Both loopback facades require exact delegation and sign real control requests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from atellagent_client.integrations.agents.anthropic_facade import AnthropicMessagesFacadeRuntime
from atellagent_client.integrations.agents.openai_facade import OpenAIResponsesFacadeRuntime
from facade_support import identity


@pytest.mark.parametrize("runtime_type", [AnthropicMessagesFacadeRuntime, OpenAIResponsesFacadeRuntime])
@pytest.mark.asyncio
async def test_real_control_request_has_device_proof_not_human_token(tmp_path, runtime_type):
    config, binding = identity(tmp_path)
    runtime = runtime_type(config, native_binding=binding, capability_token="t" * 32, port=0)
    await runtime.governance.close_async()
    request = AsyncMock(return_value=object())
    runtime.governance._gateway_session = SimpleNamespace(request_authenticated=request)
    target = config.gateway_url + "/v1/agents/boundary/model/decision"
    await runtime.governance._request_gateway_async("POST", target, workflow_context={}, json={"model": "test"})
    headers = request.call_args.kwargs["headers"]
    assert headers["X-Atellagent-Local-Authorization-Id"] == str(binding.authorization_id)
    assert headers["X-Atellagent-Device-Id"] == str(binding.device.device_id)
    assert "X-Atellagent-Local-User-Authorization" not in headers


@pytest.mark.parametrize("runtime_type", [AnthropicMessagesFacadeRuntime, OpenAIResponsesFacadeRuntime])
@pytest.mark.parametrize("field", ["tenant_id", "service_account_id", "integration_id", "gateway_url", "cert_path", "key_path"])
def test_mixed_identity_cannot_open_boundary(tmp_path, runtime_type, field):
    config, binding = identity(tmp_path)
    setattr(config, field, "https://other.example" if field == "gateway_url" else
            "/other/material.pem" if field.endswith("path") else str(uuid4()))
    with pytest.raises(ValueError, match="exact activated native binding"):
        runtime_type(config, native_binding=binding, capability_token="t" * 32)
