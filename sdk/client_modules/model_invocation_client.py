# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK model invocation and channel-ingress client methods."""

from __future__ import annotations

from typing import Any, Dict, List, Optional



class ModelInvocationClientMixin:
    def invoke_model(
        self,
        messages: List[Dict[str, Any]],
        stream: bool = False,
        workflow_context: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Send a provider-neutral model invocation request (sync)."""
        client = self._ensure_authenticated_sync()
        headers = self.auth_manager.get_auth_headers()
        headers = self._apply_workflow_headers(headers, workflow_context)
        return self.operations.invoke_model_sync(
            client,
            headers,
            messages,
            stream,
            telemetry_emitter=self.telemetry_emitter,
            telemetry_context=self.telemetry_context,
            **kwargs,
        )

    async def invoke_model_async(
        self,
        messages: List[Dict[str, Any]],
        stream: bool = False,
        workflow_context: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Send a provider-neutral model invocation request (async)."""
        session = await self._ensure_authenticated_async()
        headers = self.auth_manager.get_auth_headers()
        headers = self._apply_workflow_headers(headers, workflow_context)
        return await self.operations.invoke_model_async(
            session,
            headers,
            messages,
            stream,
            telemetry_emitter=self.telemetry_emitter,
            telemetry_context=self.telemetry_context,
            **kwargs,
        )

    def send_channel_ingress(
        self,
        *,
        event: Dict[str, Any],
        target: Optional[Dict[str, Any]] = None,
        input_data: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        channel_type: Optional[str] = None,
        provider_key: Optional[str] = None,
        adapter_key: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a channel ingress event to the gateway service-account surface."""
        self._require_channel_service_account(method_name="send_channel_ingress")
        client = self._ensure_authenticated_sync()
        headers = self.auth_manager.get_auth_headers()
        return self.operations.channel_ingress_sync(
            client,
            headers,
            event=event,
            target=target,
            input_data=input_data,
            execution_config=execution_config,
            channel_type=channel_type,
            provider_key=provider_key,
            adapter_key=adapter_key,
            idempotency_key=idempotency_key,
            telemetry_emitter=self.telemetry_emitter,
            telemetry_context=self.telemetry_context,
        )

    async def send_channel_ingress_async(
        self,
        *,
        event: Dict[str, Any],
        target: Optional[Dict[str, Any]] = None,
        input_data: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        channel_type: Optional[str] = None,
        provider_key: Optional[str] = None,
        adapter_key: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a channel ingress event to the gateway service-account surface (async)."""
        self._require_channel_service_account(method_name="send_channel_ingress_async")
        session = await self._ensure_authenticated_async()
        headers = self.auth_manager.get_auth_headers()
        return await self.operations.channel_ingress_async(
            session,
            headers,
            event=event,
            target=target,
            input_data=input_data,
            execution_config=execution_config,
            channel_type=channel_type,
            provider_key=provider_key,
            adapter_key=adapter_key,
            idempotency_key=idempotency_key,
            telemetry_emitter=self.telemetry_emitter,
            telemetry_context=self.telemetry_context,
        )


__all__ = ["ModelInvocationClientMixin"]
