# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""YAML loader for SDK service-account configuration."""

from pathlib import Path
from typing import Any, Dict

import yaml

from .config_coercion import (
    _coerce_bool,
    _env_override,
)
from .config_deployment import _parse_deployment_config
from .config_models import ServiceAccountConfig


def load_service_account_config_from_yaml(path: str) -> ServiceAccountConfig:
    """
    Load service account configuration from a YAML file with env overrides.

    YAML keys:
      gateway_mtls_url, oauth_token_url, oauth_jwks_url, client_id,
      service_account_id, tenant_id, certificate_enrollment_url,
      certificate_enrollment_expires_at,
      integration_id, placement, protocol_version,
      capabilities, packaging,
      control_source, local_guardrail_manifest_path, local_guardrail_mode,
      connected runtime path templates, integration_name, integration_type,
      integration_category, channel,
      deployment, timeout,
      api_version, contract_version

    Env overrides (if set):
      GATEWAY_MTLS_URL, ATELLAGENT_OAUTH_TOKEN_URL,
      ATELLAGENT_OAUTH_JWKS_URL, SERVICE_ACCOUNT_CLIENT_ID,
      SERVICE_ACCOUNT_ID, TELEMETRY_URL,
      ATELLAGENT_API_VERSION, ATELLAGENT_CONTRACT_VERSION,
      ATELLAGENT_TIMEOUT, ATELLAGENT_CERT_PATH, ATELLAGENT_KEY_PATH,
      ATELLAGENT_CONTROL_SOURCE, ATELLAGENT_LOCAL_GUARDRAIL_MANIFEST,
      ATELLAGENT_LOCAL_GUARDRAIL_MODE
    """
    with open(path, "r", encoding="utf-8") as f:
        data: Dict[str, Any] = yaml.safe_load(f) or {}

    allowed_yaml_keys = {
        "gateway_mtls_url",
        "oauth_token_url",
        "oauth_jwks_url",
        "client_id",
        "service_account_id",
        "integration_id",
        "tenant_id",
        "schema_version",
        "placement",
        "protocol_version",
        "capabilities",
        "packaging",
        "registration_path",
        "receive_path_template",
        "acknowledgement_path_template",
        "lease_renewal_path_template",
        "result_path_template",
        "mcp_action_path_template",
        "heartbeat_path_template",
        "drain_path_template",
        "deregistration_path_template",
        "certificate_rotation_path_template",
        "certificate_rotation_operation_path_template",
        "certificate_rotation_activation_path_template",
        "mcp_descriptor_path_template",
        "client_certificate_path",
        "client_private_key_path",
        "certificate_enrollment_url",
        "certificate_enrollment_expires_at",
        "telemetry_url",
        "api_version",
        "contract_version",
        "control_source",
        "local_guardrail_manifest_path",
        "local_guardrail_mode",
        "timeout",
        "integration_name",
        "integration_type",
        "integration_category",
        "channel",
        "deployment",
    }
    unsupported_keys = sorted(set(data.keys()) - allowed_yaml_keys)
    if unsupported_keys:
        raise ValueError(
            "Unsupported keys in service-account YAML: "
            f"{', '.join(unsupported_keys)}."
        )

    gateway_url = _env_override(data.get("gateway_mtls_url"), "GATEWAY_MTLS_URL")
    oauth_token_url = _env_override(
        data.get("oauth_token_url"), "ATELLAGENT_OAUTH_TOKEN_URL"
    )
    oauth_jwks_url = _env_override(
        data.get("oauth_jwks_url"), "ATELLAGENT_OAUTH_JWKS_URL"
    )
    client_id = _env_override(data.get("client_id"), "SERVICE_ACCOUNT_CLIENT_ID")
    service_account_id = _env_override(
        data.get("service_account_id"), "SERVICE_ACCOUNT_ID"
    )
    integration_id = _env_override(data.get("integration_id"), "ATELLAGENT_INTEGRATION_ID")
    tenant_id = _env_override(data.get("tenant_id"), "ATELLAGENT_TENANT_ID")
    telemetry_url = _env_override(data.get("telemetry_url"), "TELEMETRY_URL")
    api_version = _env_override(data.get("api_version"), "ATELLAGENT_API_VERSION")
    contract_version = _env_override(
        data.get("contract_version"),
        "ATELLAGENT_CONTRACT_VERSION",
    )
    control_source = _env_override(
        data.get("control_source"), "ATELLAGENT_CONTROL_SOURCE"
    ) or "cluster_directive"
    local_guardrail_manifest_path = _env_override(
        data.get("local_guardrail_manifest_path"),
        "ATELLAGENT_LOCAL_GUARDRAIL_MANIFEST",
    )
    local_guardrail_mode = _env_override(
        data.get("local_guardrail_mode"),
        "ATELLAGENT_LOCAL_GUARDRAIL_MODE",
    )
    if local_guardrail_manifest_path:
        candidate = Path(local_guardrail_manifest_path).expanduser()
        if not candidate.is_absolute():
            candidate = Path(path).expanduser().resolve().parent / candidate
        local_guardrail_manifest_path = str(candidate.resolve())

    cert_path = _env_override(
        data.get("client_certificate_path"), "ATELLAGENT_CERT_PATH"
    )
    key_path = _env_override(
        data.get("client_private_key_path"), "ATELLAGENT_KEY_PATH"
    )
    config_directory = Path(path).expanduser().resolve().parent
    if cert_path and not Path(cert_path).expanduser().is_absolute():
        cert_path = str((config_directory / Path(cert_path).expanduser()).resolve())
    if key_path and not Path(key_path).expanduser().is_absolute():
        key_path = str((config_directory / Path(key_path).expanduser()).resolve())
    timeout_value = _env_override(str(data.get("timeout", "")), "ATELLAGENT_TIMEOUT")
    try:
        timeout = float(timeout_value) if timeout_value else 120.0
    except ValueError:
        timeout = 120.0
    integration_name = data.get("integration_name")
    integration_type = data.get("integration_type")
    integration_category = data.get("integration_category")
    channel_section = (
        data.get("channel") if isinstance(data.get("channel"), dict) else {}
    )
    channel_type = channel_section.get("channel_type")
    channel_provider_key = channel_section.get("provider_key")
    channel_adapter_key = channel_section.get("adapter_key")
    channel_ingress_enabled = _coerce_bool(
        channel_section.get("ingress_enabled"), default=True
    )
    channel_egress_enabled = _coerce_bool(
        channel_section.get("egress_enabled"), default=True
    )
    if "adapters" in channel_section:
        raise ValueError(
            "channel.adapters is not supported; register adapter objects explicitly in customer code"
        )
    packaging = str(data.get("packaging") or "sdk").strip().lower()
    if packaging not in {"sdk", "bridge"}:
        raise ValueError("packaging must be 'sdk' or 'bridge'")
    deployment = _parse_deployment_config(
        data.get("deployment"),
        packaging=packaging,
    )
    if not gateway_url or not client_id or not oauth_token_url or not oauth_jwks_url:
        raise ValueError(
            "gateway_mtls_url, oauth_token_url, oauth_jwks_url, and client_id are required"
        )
    if not service_account_id:
        raise ValueError("service_account_id is required (provided by backend bundle)")
    if not cert_path or not key_path:
        raise ValueError("ATELLAGENT_CERT_PATH and ATELLAGENT_KEY_PATH are required")

    return ServiceAccountConfig(
        client_id=client_id,
        gateway_url=gateway_url,
        oauth_token_url=oauth_token_url,
        oauth_jwks_url=oauth_jwks_url,
        service_account_id=service_account_id,
        integration_id=integration_id,
        tenant_id=tenant_id,
        placement=str(data.get("placement") or "").strip(),
        protocol_version=str(data.get("protocol_version") or "").strip(),
        capabilities=list(data.get("capabilities") or []),
        packaging=packaging,
        registration_path=str(
            data.get("registration_path") or ServiceAccountConfig.registration_path
        ).strip(),
        receive_path_template=str(
            data.get("receive_path_template")
            or ServiceAccountConfig.receive_path_template
        ).strip(),
        acknowledgement_path_template=str(
            data.get("acknowledgement_path_template")
            or ServiceAccountConfig.acknowledgement_path_template
        ).strip(),
        lease_renewal_path_template=str(
            data.get("lease_renewal_path_template")
            or ServiceAccountConfig.lease_renewal_path_template
        ).strip(),
        result_path_template=str(
            data.get("result_path_template") or ServiceAccountConfig.result_path_template
        ).strip(),
        mcp_action_path_template=str(
            data.get("mcp_action_path_template")
            or ServiceAccountConfig.mcp_action_path_template
        ).strip(),
        heartbeat_path_template=str(
            data.get("heartbeat_path_template")
            or ServiceAccountConfig.heartbeat_path_template
        ).strip(),
        drain_path_template=str(
            data.get("drain_path_template") or ServiceAccountConfig.drain_path_template
        ).strip(),
        deregistration_path_template=str(
            data.get("deregistration_path_template")
            or ServiceAccountConfig.deregistration_path_template
        ).strip(),
        certificate_rotation_path_template=str(
            data.get("certificate_rotation_path_template")
            or ServiceAccountConfig.certificate_rotation_path_template
        ).strip(),
        certificate_rotation_operation_path_template=str(
            data.get("certificate_rotation_operation_path_template")
            or ServiceAccountConfig.certificate_rotation_operation_path_template
        ).strip(),
        certificate_rotation_activation_path_template=str(
            data.get("certificate_rotation_activation_path_template")
            or ServiceAccountConfig.certificate_rotation_activation_path_template
        ).strip(),
        mcp_descriptor_path_template=(
            str(data.get("mcp_descriptor_path_template") or "").strip() or None
        ),
        certificate_enrollment_url=data.get("certificate_enrollment_url"),
        certificate_enrollment_expires_at=data.get("certificate_enrollment_expires_at"),
        telemetry_url=telemetry_url,
        api_version=api_version,
        contract_version=contract_version,
        control_source=control_source,
        local_guardrail_manifest_path=local_guardrail_manifest_path,
        local_guardrail_mode=local_guardrail_mode,
        cert_path=cert_path,
        key_path=key_path,
        timeout=timeout,
        integration_name=integration_name,
        integration_type=integration_type,
        integration_category=integration_category,
        channel_type=channel_type,
        channel_provider_key=channel_provider_key,
        channel_adapter_key=channel_adapter_key,
        channel_ingress_enabled=channel_ingress_enabled,
        channel_egress_enabled=channel_egress_enabled,
        deployment=deployment,
    )
