# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Workload runtime settings from frozen intent, verified credentials and endpoints."""

from dataclasses import asdict
from pathlib import Path
from cryptography import x509
from atellagent_client.sdk.config_deployment import _parse_deployment_config
from atellagent_client.sdk.config_models import ServiceAccountConfig
from .errors import OnboardingClientError
from .runtime_metadata import RuntimeMetadata
from .workload_request_receipt import WorkloadRequestReceipt
from .certificate_material import parse_certificates


def build_workload_runtime_config(*, draft, receipt, stored, metadata, gateway_origin,
    deployment=None, local_guardrail_manifest_path=None):
    """Conversion is not current execution authority; Gateway checks each action.

    Local target/manifest inputs never override server-reviewed identity, profile,
    capabilities or endpoints. The caller loads and verifies committed material.
    """
    try:
        checked = WorkloadRequestReceipt.from_payload(receipt.payload(), draft=draft)
        endpoints = RuntimeMetadata.from_payload(asdict(metadata), expected_origin=draft.setup.platform_origin,
                                                 expected_gateway_origin=gateway_origin)
        credential = stored.credential
        leaf = parse_certificates(credential.certificate_pem, maximum=1)[0]
        expected_dns = f"sa.{checked.service_account_id.hex}.{draft.setup.tenant_id.hex}.identity.invalid"
        if (checked.status not in {"installed", "connected"}
            or (credential.service_account_id, credential.integration_id) != (
                checked.service_account_id, checked.integration_id)
            or list(leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value) != [x509.DNSName(expected_dns)]):
            raise ValueError("exact activated workload required")
        proposal = draft.proposal
        manifest = local_guardrail_manifest_path
        if manifest is not None and (not isinstance(manifest, str) or not manifest.strip()
            or manifest != manifest.strip() or not Path(manifest).is_absolute()):
            raise ValueError("explicit absolute local manifest path required")
        return ServiceAccountConfig(client_id=f"service-account-{checked.service_account_id}",
            platform_origin=draft.setup.platform_origin,
            tenant_id=str(draft.setup.tenant_id), service_account_id=str(checked.service_account_id),
            integration_id=str(checked.integration_id), gateway_url=endpoints.gateway_origin,
            oauth_token_url=endpoints.token_url, oauth_jwks_url=endpoints.jwks_url,
            cert_path=str(stored.certificate_path), key_path=str(stored.private_key_path),
            integration_type="mcp" if proposal.account_type == "mcp_server" else proposal.account_type,
            integration_name=proposal.display_name, capabilities=list(proposal.capabilities),
            identity_mode=proposal.identity_mode, packaging=proposal.packaging,
            deployment=_parse_deployment_config(deployment, packaging=proposal.packaging),
            control_source=proposal.control_source, local_guardrail_mode=proposal.local_guardrail_mode,
            local_guardrail_manifest_path=manifest, channel_type=proposal.channel_type,
            filter_execution_boundary=proposal.filter_execution_boundary,
            mcp_descriptor_path_template=("/v1/connected-runtimes/instances/{instance_id}/descriptors/mcp"
                                          if proposal.account_type == "mcp_server" else None))
    except (ValueError, TypeError, AttributeError, KeyError, x509.ExtensionNotFound, x509.DuplicateExtension) as exc:
        raise OnboardingClientError("onboarding_workload_runtime_configuration_invalid") from exc
