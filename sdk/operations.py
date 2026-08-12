# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""
API operations facade for the Atellagent SDK.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from atellagent_client.protocol.api import (
    CLIENT_LIBRARY_VERSION,
    DEFAULT_API_VERSION,
    DEFAULT_CONTRACT_VERSION,
    build_client_compat_headers,
    build_versioned_route,
    normalize_api_version,
    normalize_contract_version,
    strip_api_suffix,
)
from .operations_modules.agent_events import (
    emit_agent_event_async as _emit_agent_event_async,
    emit_agent_event_sync as _emit_agent_event_sync,
)
from .operations_modules.model_invocations import (
    model_invocation_async as _model_invocation_async,
    model_invocation_sync as _model_invocation_sync,
)
from .operations_modules.channels import (
    channel_ingress_async as _channel_ingress_async,
    channel_ingress_sync as _channel_ingress_sync,
)
from .operations_modules.common import (
    extract_policy_detail as _extract_policy_detail_impl,
    sanitize_model_invocation_options as _sanitize_model_invocation_options_impl,
)
from .operations_modules.mcp import (
    mcp_communicate_async as _mcp_communicate_async,
    mcp_communicate_sync as _mcp_communicate_sync,
)
from atellagent_client.sdk.telemetry import TelemetryEmitter


def _sanitize_model_invocation_options(options: Dict[str, Any]) -> Dict[str, Any]:
    return _sanitize_model_invocation_options_impl(options)


def _extract_policy_detail(payload: Any) -> Dict[str, Any]:
    return _extract_policy_detail_impl(payload)


class APIOperations:
    """Handles API operations for Atellagent Gateway."""

    def __init__(
        self,
        base_url: str,
        *,
        api_version: str = DEFAULT_API_VERSION,
        contract_version: str = DEFAULT_CONTRACT_VERSION,
        client_version: str = CLIENT_LIBRARY_VERSION,
    ):
        """
        Initialize API operations.

        Args:
            base_url: Base URL for the Atellagent Gateway.
        """
        self.base_url = strip_api_suffix(base_url)
        self.api_version = normalize_api_version(api_version)
        self.contract_version = normalize_contract_version(contract_version)
        self.client_version = str(client_version).strip() or CLIENT_LIBRARY_VERSION

    def route_path(self, path: str) -> str:
        return build_versioned_route(self.api_version, path)

    def route_url(self, path: str) -> str:
        return f"{self.base_url}{self.route_path(path)}"

    def apply_compat_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        return {
            **build_client_compat_headers(
                api_version=self.api_version,
                contract_version=self.contract_version,
                client_version=self.client_version,
            ),
            **(headers or {}),
        }

    def invoke_model_sync(
        self,
        client: httpx.Client,
        headers: Dict[str, str],
        messages: List[Dict[str, Any]],
        stream: bool = False,
        telemetry_emitter: Optional[TelemetryEmitter] = None,
        telemetry_context: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        return _model_invocation_sync(
            base_url=self.base_url,
            api_version=self.api_version,
            client=client,
            headers=self.apply_compat_headers(headers),
            messages=messages,
            stream=stream,
            telemetry_emitter=telemetry_emitter,
            telemetry_context=telemetry_context,
            **kwargs,
        )

    def mcp_communicate_sync(
        self,
        client: httpx.Client,
        headers: Dict[str, str],
        source_agent: str,
        target_agent: str,
        tool_name: str,
        arguments: Dict[str, Any],
        task_type: str = "mcp_tool",
        priority: str = "normal",
        tool_call_id: Optional[str] = None,
        action_context: Optional[Dict[str, Any]] = None,
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.2,
        telemetry_emitter: Optional[TelemetryEmitter] = None,
        telemetry_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return _mcp_communicate_sync(
            base_url=self.base_url,
            api_version=self.api_version,
            client=client,
            headers=self.apply_compat_headers(headers),
            source_agent=source_agent,
            target_agent=target_agent,
            tool_name=tool_name,
            arguments=arguments,
            task_type=task_type,
            priority=priority,
            tool_call_id=tool_call_id,
            action_context=action_context,
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            telemetry_emitter=telemetry_emitter,
            telemetry_context=telemetry_context,
        )

    async def invoke_model_async(
        self,
        session: httpx.AsyncClient,
        headers: Dict[str, str],
        messages: List[Dict[str, Any]],
        stream: bool = False,
        telemetry_emitter: Optional[TelemetryEmitter] = None,
        telemetry_context: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        return await _model_invocation_async(
            base_url=self.base_url,
            api_version=self.api_version,
            session=session,
            headers=self.apply_compat_headers(headers),
            messages=messages,
            stream=stream,
            telemetry_emitter=telemetry_emitter,
            telemetry_context=telemetry_context,
            **kwargs,
        )

    async def mcp_communicate_async(
        self,
        session: httpx.AsyncClient,
        headers: Dict[str, str],
        source_agent: str,
        target_agent: str,
        tool_name: str,
        arguments: Dict[str, Any],
        task_type: str = "mcp_tool",
        priority: str = "normal",
        tool_call_id: Optional[str] = None,
        action_context: Optional[Dict[str, Any]] = None,
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.2,
        telemetry_emitter: Optional[TelemetryEmitter] = None,
        telemetry_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await _mcp_communicate_async(
            base_url=self.base_url,
            api_version=self.api_version,
            session=session,
            headers=self.apply_compat_headers(headers),
            source_agent=source_agent,
            target_agent=target_agent,
            tool_name=tool_name,
            arguments=arguments,
            task_type=task_type,
            priority=priority,
            tool_call_id=tool_call_id,
            action_context=action_context,
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            telemetry_emitter=telemetry_emitter,
            telemetry_context=telemetry_context,
        )

    def channel_ingress_sync(
        self,
        client: httpx.Client,
        headers: Dict[str, str],
        *,
        event: Dict[str, Any],
        target: Optional[Dict[str, Any]] = None,
        input_data: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        channel_type: Optional[str] = None,
        provider_key: Optional[str] = None,
        adapter_key: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        telemetry_emitter: Optional[TelemetryEmitter] = None,
        telemetry_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return _channel_ingress_sync(
            base_url=self.base_url,
            api_version=self.api_version,
            client=client,
            headers=self.apply_compat_headers(headers),
            event=event,
            target=target,
            input_data=input_data,
            execution_config=execution_config,
            channel_type=channel_type,
            provider_key=provider_key,
            adapter_key=adapter_key,
            idempotency_key=idempotency_key,
            telemetry_emitter=telemetry_emitter,
            telemetry_context=telemetry_context,
        )

    async def channel_ingress_async(
        self,
        session: httpx.AsyncClient,
        headers: Dict[str, str],
        *,
        event: Dict[str, Any],
        target: Optional[Dict[str, Any]] = None,
        input_data: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        channel_type: Optional[str] = None,
        provider_key: Optional[str] = None,
        adapter_key: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        telemetry_emitter: Optional[TelemetryEmitter] = None,
        telemetry_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await _channel_ingress_async(
            base_url=self.base_url,
            api_version=self.api_version,
            session=session,
            headers=self.apply_compat_headers(headers),
            event=event,
            target=target,
            input_data=input_data,
            execution_config=execution_config,
            channel_type=channel_type,
            provider_key=provider_key,
            adapter_key=adapter_key,
            idempotency_key=idempotency_key,
            telemetry_emitter=telemetry_emitter,
            telemetry_context=telemetry_context,
        )

    def emit_agent_event_sync(
        self,
        client: httpx.Client,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return _emit_agent_event_sync(
            base_url=self.base_url,
            api_version=self.api_version,
            client=client,
            headers=self.apply_compat_headers(headers),
            payload=payload,
        )

    async def emit_agent_event_async(
        self,
        session: httpx.AsyncClient,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await _emit_agent_event_async(
            base_url=self.base_url,
            api_version=self.api_version,
            session=session,
            headers=self.apply_compat_headers(headers),
            payload=payload,
        )
