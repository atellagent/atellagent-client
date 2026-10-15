# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Workflow context binding and authenticated agent-control transport."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import httpx

from atellagent_client.protocol.agent_identity import has_bound_principal_identity
from atellagent_client.protocol.context import (
    apply_workflow_headers,
    get_workflow_context,
    merge_portable_workflow_context,
    normalize_portable_workflow_context,
)
from atellagent_client.sdk.client_modules.runtime_authority import (
    apply_runtime_authority_headers,
)

from .contracts import extract_contract_workflow_context


from .control_gateway_errors import ControlGatewayErrorMixin


class ControlGatewayTransportMixin(ControlGatewayErrorMixin):
    def _merge_context(
        self,
        *,
        explicit_context: Optional[Dict[str, Any]],
        principal_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        merged = merge_portable_workflow_context(
            explicit_context,
            get_workflow_context(),
        )
        merged = merge_portable_workflow_context(principal_context, merged)
        return normalize_portable_workflow_context(merged) or {}

    def _extract_principal_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return extract_contract_workflow_context(payload)

    @staticmethod
    def _has_bound_principal_context(
        workflow_context: Optional[Dict[str, Any]],
    ) -> bool:
        return has_bound_principal_identity(workflow_context)

    def _request_gateway_sync(
        self,
        method: str,
        url: str,
        *,
        workflow_context: Dict[str, Any],
        request_headers: Optional[Mapping[str, str]] = None,
        **kwargs: Any,
    ) -> Any:
        headers = apply_runtime_authority_headers(
            apply_workflow_headers({}, workflow_context=workflow_context)
        )
        if self._local_user_headers_provider is not None:
            raise RuntimeError(
                "native local user authorization requires async gateway requests"
            )
        headers.update(dict(request_headers or {}))
        return self.gateway_session.request_authenticated_sync(
            method,
            url,
            headers=headers,
            **kwargs,
        )

    async def _request_gateway_async(
        self,
        method: str,
        url: str,
        *,
        workflow_context: Dict[str, Any],
        request_headers: Optional[Mapping[str, str]] = None,
        require_local_user_authorization: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Send one governed request with retry-safe runtime context headers."""

        if "params" in kwargs:
            # The device proof binds the exact path and query string. Finalize
            # HTTP encoding before signing rather than letting HTTPX append an
            # unsigned query after the proof has been produced.
            url = str(httpx.URL(url).copy_merge_params(kwargs.pop("params")))
        headers = apply_runtime_authority_headers(
            apply_workflow_headers({}, workflow_context=workflow_context)
        )
        if (
            require_local_user_authorization
            and self._local_user_headers_provider is not None
        ):
            headers.update(await self._local_user_headers_provider())
        if self._device_proof_provider is not None:
            headers.update(self._device_proof_provider(method, url, kwargs.get("json")))
        headers.update(dict(request_headers or {}))
        return await self.gateway_session.request_authenticated(
            method,
            url,
            headers=headers,
            **kwargs,
        )
