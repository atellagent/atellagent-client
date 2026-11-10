# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Assigned MCP catalog and governed tool communication."""

from __future__ import annotations

import json
from typing import Any, Dict


from atellagent_client.protocol.api import build_versioned_route
from atellagent_client.sdk.operations import APIOperations


class ControlMCPMixin:
    async def mcp_communicate_async(
        self,
        *,
        target_binding: str,
        tool_name: str,
        arguments: Dict[str, Any],
        tool_call_id: str,
        action_key: str,
    ) -> Dict[str, Any]:
        """Invoke a governed MCP tool through this boundary's existing session."""

        operations = APIOperations(
            self.config.gateway_url,
            api_version=self.config.api_version,
            contract_version=self.config.contract_version,
        )
        return await operations.mcp_communicate_async(
            lambda method, url, **kwargs: self._request_gateway_async(
                method,
                url,
                workflow_context={},
                request_headers=kwargs.pop("headers", None),
                **kwargs,
            ),
            {},
            str(self.config.service_account_id),
            str(target_binding),
            str(tool_name),
            dict(arguments),
            tool_call_id=tool_call_id,
            action_context={"action_key": action_key},
        )

    async def mcp_catalog_async(self, *, operation: str = "definitions",
                                arguments: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """Discover servers and selected schemas through the governed session."""

        discovery_arguments = dict(arguments or {})
        if operation == "call_tool":
            # Resolution needs identity only. Business arguments travel in the
            # governed invocation POST, never in a discovery URL or access log.
            discovery_arguments["arguments"] = {}
        response = await self._request_gateway_async(
            "GET",
            f"{self.gateway_session.base_url}{build_versioned_route(self.config.api_version, '/agents/boundary/mcp-catalog')}",
            workflow_context={},
            params={"operation": operation, "arguments": json.dumps(discovery_arguments, separators=(",", ":"))},
        )
        payload = response.json() if response.content else {}
        if response.status_code >= 400:
            self._raise_gateway_error(response.status_code, payload)
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise RuntimeError("gateway MCP catalog response is invalid")
        return payload
