# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""
Authentication module for the Atellagent SDK
Handles service account authentication
"""

import time
import logging
import httpx
from typing import Dict

from .config import ServiceAccountConfig

logger = logging.getLogger(__name__)


class AuthManager:
    """Manages authentication for Atellagent Client"""

    def __init__(
        self,
        service_account_config: ServiceAccountConfig,
    ):
        """
        Initialize authentication manager

        Args:
            service_account_config: OAuth2 service account configuration
        """
        self.service_account_config = service_account_config

        # Authentication state
        self._access_token = None
        self._token_expires_at = 0

    def get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for requests"""
        return (
            {"Authorization": f"Bearer {self._access_token}"}
            if self._access_token
            else {}
        )

    def _get_token_url(self) -> str:
        """Return the explicitly configured OAuth 2.0 token endpoint."""
        token_url = str(self.service_account_config.oauth_token_url or "").strip()
        if not token_url:
            raise ValueError("oauth_token_url is required for service-account authentication")
        return token_url

    def get_token_url(self) -> str:
        """Expose the OAuth token URL for external HTTP client configuration."""
        return self._get_token_url()

    def authenticate_sync(self, client: httpx.Client) -> bool:
        """
        Authenticate using service account credentials (sync)

        Args:
            client: httpx Client instance

        Returns:
            True if authentication successful, False otherwise
        """
        try:
            token_url = self.get_token_url()

            data = {
                "grant_type": "client_credentials",
                "client_id": self.service_account_config.auth_client_id,
            }

            response = client.post(token_url, data=data)

            if response.status_code == 200:
                token_data = response.json()
                self._access_token = token_data["access_token"]
                expires_in = token_data.get("expires_in", 3600)
                self._token_expires_at = time.time() + expires_in - 60  # 60s buffer

                logger.debug(
                    f"Successfully authenticated service account {self.service_account_config.auth_client_id}"
                )
                return True
            else:
                logger.error("Authentication failed with HTTP status %s", response.status_code)
                return False

        except Exception as exc:
            logger.error("Authentication error: %s", type(exc).__name__)
            return False

    async def authenticate_async(self, session: httpx.AsyncClient) -> bool:
        """
        Authenticate using service account credentials (async)

        Args:
            session: httpx AsyncClient instance

        Returns:
            True if authentication successful, False otherwise
        """
        try:
            token_url = self.get_token_url()

            data = {
                "grant_type": "client_credentials",
                "client_id": self.service_account_config.auth_client_id,
            }

            response = await session.post(token_url, data=data)
            if response.status_code == 200:
                token_data = response.json()
                self._access_token = token_data["access_token"]
                expires_in = token_data.get("expires_in", 3600)
                self._token_expires_at = time.time() + expires_in - 60  # 60s buffer

                logger.debug(
                    f"Successfully authenticated service account {self.service_account_config.auth_client_id}"
                )
                return True
            else:
                logger.error("Authentication failed with HTTP status %s", response.status_code)
                return False

        except Exception as exc:
            logger.error("Authentication error: %s", type(exc).__name__)
            return False

    def is_token_valid(self) -> bool:
        """Check if current token is still valid"""
        return self._access_token is not None and time.time() < self._token_expires_at

    def invalidate_token(self) -> None:
        """Discard cached OAuth state after an authoritative auth rejection."""
        self._access_token = None
        self._token_expires_at = 0

    def ensure_authenticated_sync(self, client: httpx.Client) -> bool:
        """
        Ensure we have valid authentication (sync)

        Args:
            client: httpx Client instance

        Returns:
            True if authenticated, False otherwise
        """
        if not self.is_token_valid():
            return self.authenticate_sync(client)

        return True

    async def ensure_authenticated_async(self, session: httpx.AsyncClient) -> bool:
        """
        Ensure we have valid authentication (async)

        Args:
            session: httpx AsyncClient instance

        Returns:
            True if authenticated, False otherwise
        """
        if not self.is_token_valid():
            return await self.authenticate_async(session)

        return True
