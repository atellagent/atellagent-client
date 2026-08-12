# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Atellagent SDK client implementation."""

from __future__ import annotations

from typing import Optional

from atellagent_client.protocol.api import CLIENT_LIBRARY_VERSION
from atellagent_client.protocol.context import (
    apply_workflow_headers,
    get_workflow_context,
    normalize_portable_workflow_context,
    reset_workflow_context,
    set_workflow_context,
)
from atellagent_client.sdk.auth import AuthManager
from atellagent_client.sdk.config import ServiceAccountConfig
from ..operations import APIOperations
from atellagent_client.sdk.telemetry import TelemetryEmitter
from .agent_events import AgentEventsClientMixin
from .base import ClientBaseMixin
from .model_invocation_client import ModelInvocationClientMixin
from .init_helpers import (
    build_http_client_manager,
    build_telemetry_context,
    resolve_base_url,
)
from .lifecycle import ClientLifecycleMixin
from .mcp_tools import MCPToolsClientMixin
from .runtime_authority import apply_runtime_authority_headers


class WorkflowContextMixin:
    """Attach portable context while authority stays internal to transport."""

    def push_workflow_context(self, context):
        return set_workflow_context(context)

    def pop_workflow_context(self, token) -> None:
        reset_workflow_context(token)

    def _get_workflow_context(self):
        return get_workflow_context()

    def _apply_workflow_headers(self, headers, workflow_context=None):
        context = workflow_context or self._get_workflow_context()
        return apply_runtime_authority_headers(
            apply_workflow_headers(
                headers,
                workflow_context=normalize_portable_workflow_context(context),
            )
        )


class AtellagentClient(
    WorkflowContextMixin,
    ClientBaseMixin,
    ModelInvocationClientMixin,
    AgentEventsClientMixin,
    MCPToolsClientMixin,
    ClientLifecycleMixin,
):
    """Unified Atellagent Gateway client for service-account authentication."""

    def __init__(
        self,
        service_account_config: ServiceAccountConfig,
        timeout: float = 120.0,
        telemetry_emitter: Optional[TelemetryEmitter] = None,
        integration_type: Optional[str] = None,
        service_account_id: Optional[str] = None,
    ):
        """Initialize client with service account configuration."""

        self.service_account_config = service_account_config
        self.auth_manager = AuthManager(service_account_config)
        self.telemetry_emitter = telemetry_emitter

        base_url = resolve_base_url(
            service_account_config=service_account_config,
        )
        (
            self.http_client_manager,
            self._oauth_http_client_manager,
        ) = build_http_client_manager(
            auth_manager=self.auth_manager,
            base_url=base_url,
            service_account_config=service_account_config,
            timeout=timeout,
        )
        self.operations = APIOperations(
            base_url,
            api_version=service_account_config.api_version,
            contract_version=service_account_config.contract_version,
            client_version=CLIENT_LIBRARY_VERSION,
        )
        self.telemetry_context = build_telemetry_context(
            service_account_config=service_account_config,
            integration_type=integration_type,
            service_account_id=service_account_id,
        )


__all__ = ["AtellagentClient"]
