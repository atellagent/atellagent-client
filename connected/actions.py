# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Capability-scoped action client for ordinary handler leak prevention."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.gateway.session import GatewaySession
from atellagent_client.sdk.operations_modules.mcp import _extract_error_message

from .contracts import ConnectedMessage, ConnectedProtocolError
from .model_actions import ConnectedModelActions
from .agent_events import ConnectedAgentEvents


@dataclass
class _DeliveryActionContext:
    config: ServiceAccountConfig
    session: GatewaySession
    instance_id: str
    message: ConnectedMessage
    capability: str


class ConnectedActionClient(ConnectedModelActions, ConnectedAgentEvents):
    """Keep the bearer out of handler DTOs, logs, and normal application APIs.

    The participant process is a trusted boundary. Deploy untrusted handlers in
    a separate process behind an authenticated bridge boundary.
    """

    def __init__(self, context: _DeliveryActionContext) -> None:
        self._context = context
        ConnectedAgentEvents.__init__(self, context)

    def _url(self, template: str) -> str:
        path = template.format(
            instance_id=self._context.instance_id,
            message_id=self._context.message.message_id,
            lease_id=self._context.message.lease.lease_id,
        )
        if not path.startswith("/") or "://" in path:
            raise ConnectedProtocolError("connected action path is invalid")
        return f"{self._context.session.base_url}{path}"

    async def invoke_mcp(
        self,
        *,
        effect_key: str,
        request: Mapping[str, Any],
    ) -> Dict[str, Any]:
        normalized_effect_key = str(effect_key or "").strip()
        if not normalized_effect_key:
            raise ValueError("effect_key is required")
        url = self._url(self._context.config.mcp_action_path_template)
        payload = {"effect_key": normalized_effect_key, "request": dict(request)}
        headers = {"X-Atellagent-Connected-Capability": self._context.capability}
        response = await self._context.session.request_authenticated(
            "POST",
            url,
            json=payload,
            headers=headers,
        )
        if response.http_version != "HTTP/2":
            raise ConnectedProtocolError("connected action did not use HTTP/2")
        if response.status_code >= 400:
            try:
                detail = _extract_error_message(response.json())
            except Exception:
                detail = response.text
            raise ConnectedProtocolError(
                f"connected MCP action failed ({response.status_code}): {detail}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ConnectedProtocolError("connected MCP action response is not an object")
        return dict(payload)


__all__ = ["ConnectedActionClient"]
