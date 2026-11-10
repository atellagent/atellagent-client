# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Controller-independent attended management login for desktop, CLI and SDK setup."""

import asyncio
import base64
from contextlib import suppress
from dataclasses import asdict
from hashlib import sha256
import secrets
from time import monotonic

import httpx

from .bootstrap_metadata import BootstrapMetadata
from .device_login_models import login_challenge
from .device_login_poll import poll_login
from .errors import OnboardingClientError
from .oauth_http import oauth_form


class DeviceLoginClient:
    @classmethod
    async def open(cls, origin: str):
        """Open attended login against verified deployment metadata.

        The caller presents the challenge from begin(); no browser is opened here.
        Use the returned client as an asynchronous context manager.
        """
        from .bootstrap_client import fetch_bootstrap_metadata
        return cls(await fetch_bootstrap_metadata(origin))

    def __init__(self, metadata: BootstrapMetadata):
        payload = asdict(metadata)
        payload["scopes"] = list(metadata.scopes)
        self.metadata = BootstrapMetadata.from_payload(payload, expected_origin=metadata.platform_origin)
        self._http = httpx.AsyncClient(http2=True, trust_env=False,
            follow_redirects=False, timeout=15)
        self._challenge = None
        self._starting, self._closed = False, False
        self._poll_task = None

    async def begin(self):
        if self._closed:
            raise OnboardingClientError("onboarding_login_closed")
        if self._challenge is not None or self._starting:
            raise OnboardingClientError("onboarding_login_in_progress")
        self._starting = True
        try:
            return await self._begin()
        finally:
            self._starting = False

    async def _begin(self):
        verifier, started = secrets.token_urlsafe(64), monotonic()
        challenge = base64.urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        status, payload = await oauth_form(self._http, self.metadata.device_authorization_url, {
            "client_id": self.metadata.client_id, "scope": " ".join(self.metadata.scopes),
            "code_challenge": challenge, "code_challenge_method": "S256"})
        if status != 200:
            raise OnboardingClientError("onboarding_login_start_rejected", status=status)
        if self._closed:
            raise OnboardingClientError("onboarding_login_closed")
        self._challenge = login_challenge(payload, browser_url=self.metadata.browser_authorization_url,
            verifier=verifier, started_at=started)
        return self._challenge

    async def wait(self):
        if self._closed:
            raise OnboardingClientError("onboarding_login_closed")
        if self._poll_task is not None:
            raise OnboardingClientError("onboarding_login_in_progress")
        if self._challenge is None:
            raise OnboardingClientError("onboarding_login_not_started")
        self._poll_task = asyncio.create_task(poll_login(self._http, self.metadata, self._challenge))
        try:
            result = await self._poll_task
        except OnboardingClientError as exc:
            if not exc.retryable:
                self._challenge = None
            raise
        except BaseException:
            self._challenge = None
            raise
        else:
            self._challenge = None
            return result
        finally:
            self._poll_task = None

    async def close(self):
        self._closed = True
        self._challenge = None
        if self._poll_task is not None:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
