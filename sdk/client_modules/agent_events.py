# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK agent-event and heartbeat client methods."""

from __future__ import annotations

from typing import Any, Dict, Optional


class AgentEventsClientMixin:
    def emit_agent_event(
        self,
        *,
        method: str,
        extra: Optional[Dict[str, Any]] = None,
        endpoint: Optional[str] = None,
        status_code: Optional[int] = None,
        response_time_ms: Optional[int] = None,
        tokens_used: Optional[int] = None,
        policy_result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Emit a canonical agent event to gateway ingest."""
        service_account_id = self._require_agent_event_service_account()
        client = self._ensure_authenticated_sync()
        headers = self.auth_manager.get_auth_headers()
        payload = self._build_agent_event_payload(
            service_account_id=service_account_id,
            method=method,
            extra=extra or {},
            endpoint=endpoint,
            status_code=status_code,
            response_time_ms=response_time_ms,
            tokens_used=tokens_used,
            policy_result=policy_result,
            error_message=error_message,
            request_id=request_id,
        )
        return self.operations.emit_agent_event_sync(client, headers, payload)

    async def emit_agent_event_async(
        self,
        *,
        method: str,
        extra: Optional[Dict[str, Any]] = None,
        endpoint: Optional[str] = None,
        status_code: Optional[int] = None,
        response_time_ms: Optional[int] = None,
        tokens_used: Optional[int] = None,
        policy_result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Emit a canonical agent event to gateway ingest."""
        service_account_id = self._require_agent_event_service_account()
        session = await self._ensure_authenticated_async()
        headers = self.auth_manager.get_auth_headers()
        payload = self._build_agent_event_payload(
            service_account_id=service_account_id,
            method=method,
            extra=extra or {},
            endpoint=endpoint,
            status_code=status_code,
            response_time_ms=response_time_ms,
            tokens_used=tokens_used,
            policy_result=policy_result,
            error_message=error_message,
            request_id=request_id,
        )
        return await self.operations.emit_agent_event_async(session, headers, payload)


__all__ = ["AgentEventsClientMixin"]
