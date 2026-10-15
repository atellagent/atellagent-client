# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK gateway session management.
Shared gateway session/auth construction for service-account integrations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

import httpx

from atellagent_client.protocol.api import (
    CLIENT_LIBRARY_VERSION,
    build_client_compat_headers,
    strip_api_suffix,
)
from atellagent_client.sdk.auth import AuthManager
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.errors import AuthenticationError
from atellagent_client.sdk.http import HTTPClientManager
from .device_proof import DeviceProofProvider, request_device_headers, merge_request_headers
from atellagent_client.sdk.tls import (
    build_gateway_cert_validator,
    build_oauth_cert_validator,
)


@dataclass
class GatewaySession:
    config: ServiceAccountConfig
    auth_manager: AuthManager
    http_client_manager: HTTPClientManager
    oauth_http_client_manager: HTTPClientManager
    base_url: str
    device_proof_provider: DeviceProofProvider | None = None

    @classmethod
    def from_service_account_config(cls, config: ServiceAccountConfig, *,
                                    device_proof_provider: DeviceProofProvider | None = None) -> "GatewaySession":
        auth_manager = AuthManager(service_account_config=config)

        cert_tuple = None
        if config.cert_path and config.key_path:
            cert_tuple = (config.cert_path, config.key_path)

        http_client_manager = HTTPClientManager(
            timeout=config.timeout,
            cert=cert_tuple,
            server_identity_validator=build_gateway_cert_validator(config.gateway_url),
            http2=True,
        )
        token_url = auth_manager.get_token_url()
        oauth_http_client_manager = HTTPClientManager(
            timeout=config.timeout,
            cert=cert_tuple,
            server_identity_validator=build_oauth_cert_validator(token_url),
        )

        return cls(
            config=config,
            auth_manager=auth_manager,
            http_client_manager=http_client_manager,
            oauth_http_client_manager=oauth_http_client_manager,
            base_url=strip_api_suffix(config.gateway_url),
            device_proof_provider=device_proof_provider,
        )

    async def _auth_headers_async(self) -> Dict[str, str]:
        auth_session = await self.oauth_http_client_manager.get_async_client()
        if not await self.auth_manager.ensure_authenticated_async(auth_session):
            raise AuthenticationError("service-account authentication failed")
        return {
            **build_client_compat_headers(
                api_version=self.config.api_version,
                contract_version=self.config.contract_version,
                client_version=CLIENT_LIBRARY_VERSION,
            ),
            **self.auth_manager.get_auth_headers(),
        }

    async def request_authenticated(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        **kwargs: Any,
    ) -> object:
        """Use one HTTP/2 connection per request; retry only before connection.

        A response/read/write failure does not establish whether a request was
        accepted. Do not resend it. The bounded server connection lifetime
        cannot interrupt another request on a reused client connection.
        """
        extra_headers = dict(headers or {})
        refreshed_token = False
        retried_transport = False
        while True:
            auth_headers = await self._auth_headers_async()
            device_headers = request_device_headers(self.device_proof_provider,
                base_url=self.base_url, method=method, url=url, options=kwargs)
            try:
                async with self.http_client_manager.create_async_client() as client:
                    response = await client.request(
                        method,
                        url,
                        headers=merge_request_headers(auth_headers, extra_headers, device_headers),
                        **kwargs,
                    )
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if retried_transport:
                    raise
                retried_transport = True
                continue
            if response.status_code != 401 or refreshed_token:
                return response
            refreshed_token = True
            self.auth_manager.invalidate_token()

    def request_authenticated_sync(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        **kwargs: Any,
    ) -> object:
        """Use the same pre-connection-only recovery as the async path."""
        extra_headers = dict(headers or {})
        refreshed_token = False
        retried_transport = False
        while True:
            auth_headers = self._auth_headers_sync()
            device_headers = request_device_headers(self.device_proof_provider,
                base_url=self.base_url, method=method, url=url, options=kwargs)
            try:
                with self.http_client_manager.create_sync_client() as client:
                    response = client.request(
                        method,
                        url,
                        headers=merge_request_headers(auth_headers, extra_headers, device_headers),
                        **kwargs,
                    )
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if retried_transport:
                    raise
                retried_transport = True
                continue
            if response.status_code != 401 or refreshed_token:
                return response
            refreshed_token = True
            self.auth_manager.invalidate_token()

    def _auth_headers_sync(self) -> Dict[str, str]:
        auth_session = self.oauth_http_client_manager.get_sync_client()
        if not self.auth_manager.ensure_authenticated_sync(auth_session):
            raise AuthenticationError("service-account authentication failed")
        return {
            **build_client_compat_headers(
                api_version=self.config.api_version,
                contract_version=self.config.contract_version,
                client_version=CLIENT_LIBRARY_VERSION,
            ),
            **self.auth_manager.get_auth_headers(),
        }

    def _managers(self) -> list[HTTPClientManager]:
        managers = [self.http_client_manager, self.oauth_http_client_manager]
        unique: list[HTTPClientManager] = []
        seen: set[int] = set()
        for manager in managers:
            key = id(manager)
            if key in seen:
                continue
            seen.add(key)
            unique.append(manager)
        return unique

    def close_sync(self) -> None:
        for manager in self._managers():
            manager.close_sync()

    async def close_async(self) -> None:
        for manager in self._managers():
            await manager.close_async()

    def __enter__(self) -> "GatewaySession":
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        self.close_sync()

    async def __aenter__(self) -> "GatewaySession":
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        await self.close_async()
