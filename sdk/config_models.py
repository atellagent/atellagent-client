# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Typed configuration models for the Atellagent SDK."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from atellagent_client.protocol.api import (
    DEFAULT_API_VERSION,
    DEFAULT_CONTRACT_VERSION,
    normalize_api_version,
    normalize_contract_version,
)


@dataclass
class BaseDeploymentConfig:
    type: str


@dataclass
class SDKDeploymentConfig(BaseDeploymentConfig):
    type: str = "sdk"


@dataclass
class BridgeDeploymentConfig(BaseDeploymentConfig):
    type: str = "bridge"
    target_transport: str = "handler"
    target_url: Optional[str] = None
    target_unix_socket: Optional[str] = None
    target_command: Optional[str] = None
    target_args: List[str] = field(default_factory=list)
    target_env_map: Dict[str, str] = field(default_factory=dict)
    upstream_headers: Dict[str, str] = field(default_factory=dict)
    upstream_auth_header: Optional[str] = None
    upstream_auth_token_env: Optional[str] = None
    upstream_cert_path: Optional[str] = None
    upstream_key_path: Optional[str] = None
    upstream_ca_path: Optional[str] = None


DeploymentConfig = Union[
    SDKDeploymentConfig,
    BridgeDeploymentConfig,
]


@dataclass
class ServiceAccountConfig:
    """Configuration for service account OAuth2 authentication (mTLS path/env only)."""

    client_id: str
    gateway_url: str
    oauth_token_url: Optional[str] = None
    oauth_jwks_url: Optional[str] = None
    service_account_id: Optional[str] = None  # required immutable id from backend
    integration_id: Optional[str] = None
    tenant_id: Optional[str] = (
        None  # required only while constructing an enrollment CSR
    )
    placement: str = "connected"
    protocol_version: str = "v1"
    capabilities: List[str] = field(default_factory=list)
    packaging: str = "sdk"
    registration_path: str = "/v1/connected-runtimes/instances"
    receive_path_template: str = (
        "/v1/connected-runtimes/instances/{instance_id}/receive"
    )
    acknowledgement_path_template: str = (
        "/v1/connected-runtimes/instances/{instance_id}/messages/"
        "{message_id}/acknowledgements"
    )
    lease_renewal_path_template: str = (
        "/v1/connected-runtimes/instances/{instance_id}/messages/"
        "{message_id}/lease-renewals"
    )
    result_path_template: str = (
        "/v1/connected-runtimes/instances/{instance_id}/messages/"
        "{message_id}/results"
    )
    mcp_action_path_template: str = (
        "/v1/connected-runtimes/instances/{instance_id}/messages/"
        "{message_id}/leases/{lease_id}/actions/mcp"
    )
    heartbeat_path_template: str = (
        "/v1/connected-runtimes/instances/{instance_id}/heartbeat"
    )
    drain_path_template: str = (
        "/v1/connected-runtimes/instances/{instance_id}/drain"
    )
    deregistration_path_template: str = (
        "/v1/connected-runtimes/instances/{instance_id}"
    )
    certificate_rotation_path_template: str = (
        "/v1/connected-runtimes/instances/{instance_id}/certificate-rotations"
    )
    certificate_rotation_operation_path_template: str = (
        "/v1/connected-runtimes/instances/{instance_id}/certificate-rotations/"
        "{operation_id}"
    )
    certificate_rotation_activation_path_template: str = (
        "/v1/connected-runtimes/instances/{instance_id}/certificate-rotations/"
        "{operation_id}/activate"
    )
    mcp_descriptor_path_template: Optional[str] = None
    certificate_enrollment_url: Optional[str] = None
    certificate_enrollment_expires_at: Optional[str] = None
    telemetry_url: Optional[str] = None  # optional telemetry ingestion endpoint
    api_version: str = DEFAULT_API_VERSION
    contract_version: str = DEFAULT_CONTRACT_VERSION
    control_source: str = "cluster_directive"
    local_guardrail_manifest_path: Optional[str] = None
    local_guardrail_mode: Optional[str] = None
    cert_path: Optional[str] = None
    key_path: Optional[str] = None
    timeout: float = 120.0
    integration_name: Optional[str] = None
    integration_type: Optional[str] = None
    integration_category: Optional[str] = None
    channel_type: Optional[str] = None
    channel_provider_key: Optional[str] = None
    channel_adapter_key: Optional[str] = None
    channel_ingress_enabled: bool = True
    channel_egress_enabled: bool = True
    deployment: DeploymentConfig = field(default_factory=SDKDeploymentConfig)

    def __post_init__(self):
        self.api_version = normalize_api_version(self.api_version)
        self.contract_version = normalize_contract_version(self.contract_version)
        has_paths = self.cert_path and self.key_path
        if not has_paths:
            raise ValueError(
                "mTLS client certificate and key are required via ATELLAGENT_CERT_PATH/ATELLAGENT_KEY_PATH"
            )
        if not self.service_account_id:
            raise ValueError("service_account_id is required for service accounts")
        if not self.integration_id:
            raise ValueError("integration_id is required for connected service accounts")
        if self.placement != "connected":
            raise ValueError("service-account runtime placement must be 'connected'")
        if self.protocol_version != "v1":
            raise ValueError("connected runtime protocol_version must be 'v1'")
        if self.packaging not in {"sdk", "bridge"}:
            raise ValueError("connected runtime packaging must be 'sdk' or 'bridge'")
        if getattr(self.deployment, "type", None) != self.packaging:
            raise ValueError("deployment.type must match connected runtime packaging")
        if not self.tenant_id:
            raise ValueError("tenant_id is required for connected service accounts")
        required_paths = {
            "registration_path": self.registration_path,
            "receive_path_template": self.receive_path_template,
            "acknowledgement_path_template": self.acknowledgement_path_template,
            "lease_renewal_path_template": self.lease_renewal_path_template,
            "result_path_template": self.result_path_template,
            "mcp_action_path_template": self.mcp_action_path_template,
            "heartbeat_path_template": self.heartbeat_path_template,
            "drain_path_template": self.drain_path_template,
            "deregistration_path_template": self.deregistration_path_template,
            "certificate_rotation_path_template": self.certificate_rotation_path_template,
            "certificate_rotation_operation_path_template": self.certificate_rotation_operation_path_template,
            "certificate_rotation_activation_path_template": self.certificate_rotation_activation_path_template,
        }
        for name, value in required_paths.items():
            if not str(value or "").startswith("/") or "://" in str(value):
                raise ValueError(f"{name} must be an absolute gateway path")
        normalized_capabilities = [
            str(value).strip() for value in self.capabilities if str(value).strip()
        ]
        if len(normalized_capabilities) != len(set(normalized_capabilities)):
            raise ValueError("connected runtime capabilities must be unique")
        self.capabilities = normalized_capabilities
        if not self.integration_type:
            raise ValueError("integration_type is required for service accounts")
        if self.integration_type not in {
            "agent",
            "mcp",
            "channel",
            "model",
            "ml_filter",
            "workflow_runtime",
        }:
            raise ValueError(
                "integration_type must be 'agent', 'mcp', 'channel', "
                "'model', 'ml_filter', or 'workflow_runtime'"
            )
        self.control_source = str(self.control_source or "").strip().lower()
        if self.control_source not in {"cluster_directive", "local_manifest"}:
            raise ValueError(
                "control_source must be 'cluster_directive' or 'local_manifest'"
            )
        manifest_path = str(self.local_guardrail_manifest_path or "").strip() or None
        self.local_guardrail_manifest_path = manifest_path
        local_mode = str(self.local_guardrail_mode or "").strip().lower() or None
        self.local_guardrail_mode = local_mode
        if self.control_source == "local_manifest":
            if self.integration_type != "mcp":
                raise ValueError(
                    "local_manifest control is supported only for connected MCP runtimes"
                )
            if not manifest_path:
                raise ValueError(
                    "local_guardrail_manifest_path is required for local_manifest control"
                )
            if local_mode not in {"observe", "enforce"}:
                raise ValueError(
                    "local_guardrail_mode must be 'observe' or 'enforce' for local_manifest control"
                )
        elif manifest_path or local_mode:
            raise ValueError(
                "local guardrail settings are valid only with control_source=local_manifest"
            )

    @property
    def auth_client_id(self) -> str:
        return self.client_id
