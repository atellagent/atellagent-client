"""Runtime configuration is derived from exact approved state, not imported YAML."""

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from atellagent_client.onboarding import native_runtime_config as module
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.runtime_metadata import RuntimeMetadata
from native_runtime_binding_support import runtime_case


def setup_case(tmp_path):
    runtime = runtime_case(tmp_path)
    assignment = replace(runtime.case.assignment, status="installed",
                         owner_consented_at=datetime.now(timezone.utc))
    issuer = "https://mtls.auth.example:9443/auth/realms/customer"
    metadata = RuntimeMetadata(runtime.case.draft.platform_origin, runtime.binding.gateway_origin,
        issuer, issuer + "/protocol/openid-connect/token", issuer + "/protocol/openid-connect/certs")
    return SimpleNamespace(draft=runtime.case.draft, assignment=assignment,
                           binding=runtime.binding, metadata=metadata)


def test_approved_state_supplies_machine_config_without_human_session(tmp_path):
    case = setup_case(tmp_path)
    config = module.build_native_runtime_config(**vars(case))
    assert config.tenant_id == str(case.binding.device.tenant_id)
    assert config.service_account_id == str(case.binding.service_account_id)
    assert config.integration_id == str(case.binding.integration_id)
    assert config.gateway_url == case.metadata.gateway_origin
    assert config.oauth_token_url == case.metadata.token_url
    assert config.oauth_jwks_url == case.metadata.jwks_url
    assert config.cert_path == str(case.binding.stored.certificate_path)
    assert config.key_path == str(case.binding.stored.private_key_path)
    assert config.native_host == case.assignment.proposal.native_host
    assert config.capabilities == ["agent.control"]
    assert not hasattr(config, "local_host_user_authorization")


@pytest.mark.parametrize("change", ["pending", "consent", "setup", "binding", "origin", "issuer"])
def test_mixed_or_unapproved_state_is_rejected(tmp_path, change):
    case = setup_case(tmp_path)
    if change == "pending":
        case.assignment = replace(case.assignment, status="pending")
    elif change == "consent":
        case.assignment = replace(case.assignment, owner_consented_at=None)
    elif change == "setup":
        case.assignment = replace(case.assignment, onboarding_session_id=uuid4())
    elif change == "binding":
        case.binding = replace(case.binding, controller_profile_id=uuid4())
    elif change == "origin":
        case.metadata = replace(case.metadata, platform_origin="https://other.example")
    else:
        case.metadata = replace(case.metadata, token_url="https://other.example/token")
    with pytest.raises(OnboardingClientError):
        module.build_native_runtime_config(**vars(case))


@pytest.mark.asyncio
async def test_open_revalidates_binding_and_current_assignment_before_config(tmp_path, monkeypatch):
    case = setup_case(tmp_path)
    context = SimpleNamespace(load=Mock(return_value=case.draft), client=SimpleNamespace(
        assignment=AsyncMock(return_value=case.assignment), runtime_metadata=AsyncMock(return_value=case.metadata)))
    opening = AsyncMock(return_value=case.binding)
    monkeypatch.setattr(module, "open_native_runtime_binding", opening)
    result = await module.open_native_runtime(context, case.assignment.request_id)
    opening.assert_awaited_once_with(context, case.assignment.request_id)
    context.client.assignment.assert_awaited_once_with(case.assignment.request_id)
    context.client.runtime_metadata.assert_awaited_once_with(
        platform_origin=case.draft.platform_origin, gateway_origin=case.binding.gateway_origin)
    assert result.binding is case.binding
    assert result.config.integration_id == str(case.assignment.integration_id)


@pytest.mark.asyncio
async def test_missing_current_assignment_does_not_fetch_runtime_metadata(tmp_path, monkeypatch):
    case = setup_case(tmp_path)
    context = SimpleNamespace(load=Mock(return_value=case.draft), client=SimpleNamespace(
        assignment=AsyncMock(return_value=None), runtime_metadata=AsyncMock()))
    monkeypatch.setattr(module, "open_native_runtime_binding", AsyncMock(return_value=case.binding))
    with pytest.raises(OnboardingClientError, match="onboarding_native_assignment_missing"):
        await module.open_native_runtime(context, case.assignment.request_id)
    context.client.runtime_metadata.assert_not_awaited()
