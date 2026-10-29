"""Public SDK factory opens approved machine identity, not caller-owned credentials."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from atellagent_client import create_service_account_client
from atellagent_client.onboarding.errors import OnboardingClientError
from test_workload_setup_flow import flow_case
from test_workload_credential_store import stored_case
from test_workload_proof_client import case
from test_workload_management import draft


@pytest.mark.asyncio
async def test_public_factory_constructs_real_sdk_with_retained_machine_credentials(flow_case):
    c = flow_case
    c.state.status = "issued"
    async with await create_service_account_client(flow=c.flow, timeout=45) as client:
        config = client.service_account_config
        assert config.service_account_id == str(c.credential.service_account_id)
        assert config.integration_id == str(c.credential.integration_id)
        assert config.cert_path == str(c.credentials.load(**c.args).certificate_path)
        assert client.gateway_session.config is config
        assert client.auth_manager.get_token_url() == config.oauth_token_url
        assert config.timeout == 45 and client.telemetry_emitter is None
        assert not hasattr(config, "local_host_user_authorization")
        issuer = SimpleNamespace(post=AsyncMock(return_value=httpx.Response(200,
            json={"access_token": "fixture-machine-token", "expires_in": 300})))
        assert await client.auth_manager.authenticate_async(issuer)
        client.auth_manager.invalidate_token()
        assert await client.auth_manager.authenticate_async(issuer)
        assert issuer.post.await_count == 2
        assert issuer.post.await_args.args == (config.oauth_token_url,)
        assert issuer.post.await_args.kwargs["data"] == {
            "grant_type": "client_credentials", "client_id": f"service-account-{c.credential.service_account_id}"}
    c.state.calls.clear()
    async with await create_service_account_client(flow=c.flow) as reopened:
        assert reopened.service_account_config.cert_path == config.cert_path
    assert not any(path.endswith("/claim") or path.endswith("/certificate") for path in c.state.calls)


@pytest.mark.asyncio
async def test_factory_does_not_turn_pending_request_into_runtime(flow_case):
    with pytest.raises(OnboardingClientError, match="runtime_not_activated"):
        await create_service_account_client(flow=flow_case.flow)
    assert all(path.endswith("/status") for path in flow_case.state.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf"), "30"])
async def test_invalid_local_timeout_is_rejected_before_network(flow_case, timeout):
    with pytest.raises(ValueError): await create_service_account_client(flow=flow_case.flow, timeout=timeout)
    assert flow_case.state.calls == []


def test_removed_identity_and_certificate_arguments_are_not_accepted(flow_case):
    with pytest.raises(TypeError):
        create_service_account_client(flow=flow_case.flow, client_id="other", cert_path="other.pem")
    assert flow_case.state.calls == []
