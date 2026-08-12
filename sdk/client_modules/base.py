# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Shared SDK client mixin helpers for auth, HTTP clients, and payload coercion."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from atellagent_client.sdk.errors import AuthenticationError
from atellagent_client.sdk.http import HTTPClientManager


class ClientBaseMixin:
    def _sync_clients(self):
        client = self.http_client_manager.get_sync_client()
        auth_client = (
            self._oauth_http_client_manager.get_sync_client()
            if self._oauth_http_client_manager
            else client
        )
        return client, auth_client

    async def _async_clients(self):
        session = await self.http_client_manager.get_async_client()
        auth_session = (
            await self._oauth_http_client_manager.get_async_client()
            if self._oauth_http_client_manager
            else session
        )
        return session, auth_session

    def _ensure_authenticated_sync(self):
        client, auth_client = self._sync_clients()
        if not self.auth_manager.ensure_authenticated_sync(auth_client):
            raise AuthenticationError("Failed to authenticate")
        return client

    async def _ensure_authenticated_async(self):
        session, auth_session = await self._async_clients()
        if not await self.auth_manager.ensure_authenticated_async(auth_session):
            raise AuthenticationError("Failed to authenticate")
        return session

    def _require_channel_service_account(self, *, method_name: str) -> None:
        if not self.service_account_config:
            raise AuthenticationError(
                "Service account configuration is required for channel ingress"
            )
        integration_type = (self.service_account_config.integration_type or "").strip().lower()
        if integration_type != "channel":
            raise ValueError(
                f"{method_name} requires a channel service account (integration_type='channel')"
            )

    def _require_agent_event_service_account(self) -> str:
        if not self.service_account_config:
            raise AuthenticationError(
                "Service account configuration is required for agent event ingest"
            )
        service_account_id = self.telemetry_context.get("service_account_id")
        if not service_account_id:
            raise ValueError("service_account_id is required to emit agent events")
        return service_account_id

    def _build_agent_event_payload(
        self,
        *,
        service_account_id: str,
        method: str,
        extra: Dict[str, Any],
        endpoint: str | None,
        status_code: int | None,
        response_time_ms: int | None,
        tokens_used: int | None,
        policy_result: Dict[str, Any] | None,
        error_message: str | None,
        request_id: str | None,
    ) -> Dict[str, Any]:
        return {
            "integration_type": "agent",
            "service_account_id": service_account_id,
            "auth_client_id": self.telemetry_context.get("auth_client_id"),
            "method": method,
            "endpoint": endpoint,
            "status_code": status_code,
            "response_time_ms": response_time_ms,
            "tokens_used": tokens_used,
            "policy_result": policy_result,
            "error_message": error_message,
            "request_id": request_id,
            "extra": extra,
        }

    def _coerce_tool_arguments(self, arguments: Any) -> Dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        if arguments is None:
            return {}
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {"input": arguments}
        return {"input": arguments}

    def _extract_mcp_response_content(self, payload: Any) -> str:
        raw_response = payload.get("response") if isinstance(payload, dict) else payload
        if raw_response is None:
            return ""
        if isinstance(raw_response, dict):
            content = raw_response.get("content")
            if content is None:
                return json.dumps(raw_response)
            return content if isinstance(content, str) else json.dumps(content)
        if isinstance(raw_response, str):
            try:
                parsed = json.loads(raw_response)
            except Exception:
                return raw_response
            if isinstance(parsed, dict):
                content = parsed.get("content")
                if content is None:
                    return json.dumps(parsed)
                return content if isinstance(content, str) else json.dumps(content)
            return json.dumps(parsed)
        return str(raw_response)

    def _resolve_source_agent_id(self, source_agent: str | None) -> str:
        return (
            source_agent
            or getattr(self.service_account_config, "service_account_id", None)
            or getattr(self.service_account_config, "integration_name", None)
            or "agent"
        )

    def _http_managers(self) -> List[HTTPClientManager]:
        managers: List[HTTPClientManager] = [self.http_client_manager]
        if self._oauth_http_client_manager is not None:
            managers.append(self._oauth_http_client_manager)
        unique: List[HTTPClientManager] = []
        seen: set[int] = set()
        for manager in managers:
            key = id(manager)
            if key in seen:
                continue
            seen.add(key)
            unique.append(manager)
        return unique


__all__ = ["ClientBaseMixin"]
