# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Construct native runtime configuration from exact reviewed server state."""

import asyncio
from dataclasses import asdict, dataclass

from atellagent_client.sdk.config_models import ServiceAccountConfig, SDKDeploymentConfig, BridgeDeploymentConfig
from .device_assignments import DeviceAssignment
from .errors import OnboardingClientError
from .native_runtime_binding import NativeRuntimeBinding, open_native_runtime_binding
from .runtime_metadata import RuntimeMetadata


def build_native_runtime_config(*, draft, assignment, binding: NativeRuntimeBinding, metadata: RuntimeMetadata):
    try:
        checked = DeviceAssignment.from_payload(assignment.payload(), device=binding.device)
        endpoints = RuntimeMetadata.from_payload(asdict(metadata), expected_origin=draft.platform_origin,
                                                 expected_gateway_origin=binding.gateway_origin)
        if (checked.status not in {"installed", "connected"} or checked.owner_consented_at is None
                or checked.proposal != draft.proposal or checked.onboarding_session_id != draft.setup_id
                or (draft.tenant_id, draft.device_id, draft.owner_user_id) != (
                    binding.device.tenant_id, binding.device.device_id, binding.device.owner_user_id)
                or any(getattr(checked, name) != getattr(binding, name) for name in (
                    "request_id", "service_account_id", "integration_id", "agent_principal_id", "binding_id", "controller_profile_id"))):
            raise ValueError("exact activated reviewed runtime required")
        proposal = checked.proposal
        deployment = SDKDeploymentConfig() if proposal.packaging == "sdk" else BridgeDeploymentConfig()
        return ServiceAccountConfig(client_id=f"service-account-{binding.service_account_id}",
            platform_origin=draft.platform_origin,
            tenant_id=str(binding.device.tenant_id), service_account_id=str(binding.service_account_id),
            integration_id=str(binding.integration_id), gateway_url=endpoints.gateway_origin,
            oauth_token_url=endpoints.token_url, oauth_jwks_url=endpoints.jwks_url,
            cert_path=str(binding.stored.certificate_path), key_path=str(binding.stored.private_key_path),
            integration_type="agent", integration_name=proposal.display_name, native_host=proposal.native_host,
            capabilities=list(checked.capabilities), identity_mode="boundary_identity_only",
            packaging=proposal.packaging, deployment=deployment)
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise OnboardingClientError("onboarding_native_runtime_configuration_invalid") from exc


@dataclass(frozen=True)
class NativeRuntimeSetup:
    config: ServiceAccountConfig
    binding: NativeRuntimeBinding


async def open_native_runtime(context, request_id):
    draft = await asyncio.to_thread(context.load, request_id)
    binding = await open_native_runtime_binding(context, request_id)
    assignment = await context.client.assignment(request_id)
    if assignment is None:
        raise OnboardingClientError("onboarding_native_assignment_missing")
    metadata = await context.client.runtime_metadata(platform_origin=draft.platform_origin,
                                                    gateway_origin=binding.gateway_origin)
    config = build_native_runtime_config(draft=draft, assignment=assignment, binding=binding, metadata=metadata)
    return NativeRuntimeSetup(config, binding)
