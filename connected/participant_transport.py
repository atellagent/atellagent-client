# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Authenticated HTTP/2 requests for connected runtime and rotation traffic."""

from __future__ import annotations

from typing import Any, Mapping, Optional


from .contracts import (
    ConnectedMessage,
    ConnectedProtocolError,
)
from .participant_protocol import ConnectedHTTPError


class ConnectedTransportMixin:
    def _url(self, path: str, *, message: Optional[ConnectedMessage] = None) -> str:
        rendered = str(path).format(
            instance_id=self._instance_id or "",
            message_id=message.message_id if message else "",
            lease_id=message.lease.lease_id if message else "",
        )
        if not rendered.startswith("/") or "://" in rendered:
            raise ConnectedProtocolError("connected runtime path is invalid")
        return f"{self.session.base_url}{rendered}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        message: Optional[ConnectedMessage] = None,
        json: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        if self._device_registration_refresher is not None:
            await self._device_registration_refresher()
        url = self._url(path, message=message)
        payload = dict(json) if json is not None else None
        response = await self.session.request_authenticated(
            method,
            url,
            json=payload,
        )
        if response.http_version != "HTTP/2":
            raise ConnectedProtocolError("connected runtime request did not use HTTP/2")
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise ConnectedHTTPError(
                response.status_code, str(detail or "request failed")
            )
        return response

    async def _rotation_request(
        self,
        method: str,
        path_template: str,
        *,
        operation_id: Optional[str] = None,
        json: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        if self._device_registration_refresher is not None:
            await self._device_registration_refresher()
        rendered = str(path_template).format(
            instance_id=self._instance_id or "",
            operation_id=operation_id or "",
        )
        if not rendered.startswith("/") or "://" in rendered:
            raise ConnectedProtocolError("certificate rotation path is invalid")
        url = f"{self.session.base_url}{rendered}"
        payload = dict(json) if json is not None else None
        response = await self.session.request_authenticated(
            method,
            url,
            json=payload,
        )
        if response.http_version != "HTTP/2":
            raise ConnectedProtocolError(
                "certificate rotation request did not use HTTP/2"
            )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise ConnectedHTTPError(
                response.status_code,
                str(detail or "certificate rotation request failed"),
            )
        return response
