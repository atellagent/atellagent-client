# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Connected participant registration and MCP descriptor publication."""

from __future__ import annotations


from atellagent_client.protocol.api import CLIENT_LIBRARY_VERSION

from .contracts import (
    ConnectedProtocolError,
)
from .participant_protocol import strict_object as _strict_object


class ConnectedRegistrationMixin:
    async def _register(self) -> None:
        async with self._registration_lock:
            if self._instance_id is not None:
                return
            response = await self._request(
                "POST",
                self.config.registration_path,
                json={
                    "instance_key": self.instance_key,
                    "protocol_version": self.config.protocol_version,
                    "client_version": CLIENT_LIBRARY_VERSION,
                    "capabilities": self.config.capabilities,
                },
            )
            payload = _strict_object(
                response.json(),
                {
                    "instance_id",
                    "protocol_version",
                    "presence_status",
                    "registered_at",
                    "receive_path",
                    "heartbeat_path",
                    "drain_path",
                },
                "registration response",
            )
            if payload.get("protocol_version") != "v1":
                raise ConnectedProtocolError("gateway selected an unsupported protocol")
            self._instance_id = str(payload.get("instance_id") or "").strip()
            if not self._instance_id:
                raise ConnectedProtocolError("registration response has no instance_id")
            try:
                if self._mcp_manifest is not None:
                    await self._publish_mcp_descriptor()
            except Exception:
                self._instance_id = None
                raise

    async def _publish_mcp_descriptor(self) -> None:
        if not self.config.mcp_descriptor_path_template or self._mcp_manifest is None:
            return
        response = await self._request(
            "PUT",
            self.config.mcp_descriptor_path_template,
            json={
                "protocol_version": self.config.protocol_version,
                "manifest": self._mcp_manifest,
                "expected_previous_manifest_hash": None,
            },
        )
        _strict_object(
            response.json(),
            {
                "integration_id",
                "manifest_hash",
                "descriptor_revision",
                "tool_count",
                "published_at",
            },
            "MCP descriptor response",
        )

    async def _ensure_registered(self) -> None:
        if self._instance_id is None and not self._stop_event.is_set():
            await self._register()
