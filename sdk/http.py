# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""HTTP client helpers for the Atellagent SDK."""

import atexit
import asyncio
from contextlib import suppress
import logging
import os
import signal
import ssl
from typing import Callable, Optional, Set, Tuple, Union
import weakref

import httpx

_MANAGERS = weakref.WeakSet()
_CLEANUP_REGISTERED = False
logger = logging.getLogger(__name__)


def _run_all_cleanups():
    for mgr in list(_MANAGERS):
        with suppress(Exception):
            mgr._cleanup_temp()


def _register_global_cleanup():
    global _CLEANUP_REGISTERED
    if _CLEANUP_REGISTERED:
        return
    _CLEANUP_REGISTERED = True
    atexit.register(_run_all_cleanups)
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(Exception):
            signal.signal(sig, _signal_wrapper(signal.getsignal(sig)))


def _signal_wrapper(prev_handler):
    def handler(signum, frame):
        _run_all_cleanups()
        if callable(prev_handler):
            prev_handler(signum, frame)

    return handler


class HTTPClientManager:
    """Manages HTTP clients for sync and async operations"""

    def __init__(
        self,
        timeout: float = 30.0,
        cert: Optional[Tuple[str, str]] = None,
        verify: Optional[Union[bool, str]] = None,
        server_identity_validator: Optional[Callable[[object], bool]] = None,
        http2: bool = False,
    ):
        """
        Initialize HTTP client manager

        Args:
            timeout: Request timeout in seconds
            cert: Tuple of (cert_path, key_path) for mTLS
            verify: CA bundle path or bool to enable/disable verification
        """
        self.timeout = timeout
        self.http2 = bool(http2)
        self.verify_ssl = True if verify is None else verify
        self._temp_files = []

        self._cert_tuple: Optional[Tuple[str, str]] = cert

        self._verify_target: Union[bool, str, None]
        if self.verify_ssl is False:
            self._verify_target = False
        else:
            self._verify_target = self.verify_ssl

        self._sync_client: Optional[httpx.Client] = None
        self._async_client: Optional[httpx.AsyncClient] = None
        self._pending_async_close_tasks: Set[asyncio.Task] = set()
        self._server_identity_validator = server_identity_validator
        self._ssl_context: Optional[ssl.SSLContext] = None
        if self._cert_tuple and all(self._cert_tuple):
            if self._verify_target is False:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            else:
                cafile = (
                    self._verify_target
                    if isinstance(self._verify_target, str)
                    else None
                )
                context = ssl.create_default_context(cafile=cafile)
            context.load_cert_chain(*self._cert_tuple)
            self._ssl_context = context
        _MANAGERS.add(self)
        _register_global_cleanup()

    def _validate_ssl_object(self, ssl_object: Optional[object]) -> None:
        if not self._server_identity_validator:
            return
        if not self._server_identity_validator(ssl_object):
            details = ""
            cert = (
                getattr(ssl_object, "getpeercert", lambda: None)()
                if ssl_object
                else None
            )
            if cert:
                subject = cert.get("subject")
                sans = [
                    val for name, val in cert.get("subjectAltName", []) if name == "DNS"
                ]
                details = f" Peer subject={subject} SANs={sans}"
            logger.error("TLS identity check failed.%s", details)
            raise RuntimeError(f"Gateway certificate verification failed.{details}")

    def _extract_httpx_ssl_object(self, response: httpx.Response) -> Optional[object]:
        extensions = response.extensions or {}
        for key in ("tls", "network_stream", "_network_stream"):
            candidate = extensions.get(key)
            if candidate is None:
                continue
            if isinstance(candidate, dict) and candidate.get("ssl_object"):
                return candidate.get("ssl_object")
            if hasattr(candidate, "get_extra_info"):
                return candidate.get_extra_info("ssl_object")
            stream = getattr(candidate, "stream", None)
            if stream and hasattr(stream, "get_extra_info"):
                return stream.get_extra_info("ssl_object")
        return None

    def _validate_httpx_response(self, response: httpx.Response) -> None:
        if not self._server_identity_validator:
            return
        ssl_object = self._extract_httpx_ssl_object(response)
        if ssl_object is None:
            raise RuntimeError("Unable to inspect gateway TLS certificate")
        self._validate_ssl_object(ssl_object)

    async def _validate_httpx_response_async(self, response: httpx.Response) -> None:
        self._validate_httpx_response(response)

    def get_sync_client(self) -> httpx.Client:
        """Get or create synchronous HTTP client"""
        if self._sync_client is None:
            self._sync_client = httpx.Client(
                timeout=self.timeout,
                verify=self._ssl_context or self._verify_target,
                cert=None if self._ssl_context else self._cert_tuple,
                trust_env=False,
                http2=self.http2,
                event_hooks={"response": [self._validate_httpx_response]},
            )
        return self._sync_client

    async def get_async_client(self) -> httpx.AsyncClient:
        """Get or create asynchronous HTTP client"""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                timeout=self.timeout,
                verify=self._ssl_context or self._verify_target,
                cert=None if self._ssl_context else self._cert_tuple,
                trust_env=False,
                http2=self.http2,
                event_hooks={"response": [self._validate_httpx_response_async]},
            )
        return self._async_client

    def _track_async_close_task(self, task: asyncio.Task) -> None:
        self._pending_async_close_tasks.add(task)

        def _done(done_task: asyncio.Task) -> None:
            self._pending_async_close_tasks.discard(done_task)
            self._maybe_cleanup_temp()

        task.add_done_callback(_done)

    def _maybe_cleanup_temp(self) -> None:
        if (
            self._sync_client is None
            and self._async_client is None
            and not self._pending_async_close_tasks
        ):
            self._cleanup_temp()

    def _close_async_client_from_sync(self) -> None:
        async_client = self._async_client
        if async_client is None:
            return
        self._async_client = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(async_client.aclose())
            return
        task = loop.create_task(async_client.aclose())
        self._track_async_close_task(task)

    def close_sync(self):
        """Close both sync and async HTTP clients from sync code."""
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None
        self._close_async_client_from_sync()
        self._maybe_cleanup_temp()

    async def close_async(self):
        """Close both sync and async HTTP clients from async code."""
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None
        if self._async_client:
            await self._async_client.aclose()
            self._async_client = None
        if self._pending_async_close_tasks:
            await asyncio.gather(
                *list(self._pending_async_close_tasks), return_exceptions=True
            )
        self._maybe_cleanup_temp()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        self.close_sync()

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb):
        await self.close_async()

    def _cleanup_temp(self):
        for path in self._temp_files:
            with suppress(OSError):
                os.remove(path)
        self._temp_files = []
