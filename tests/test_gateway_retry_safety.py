"""Transport recovery must not duplicate uncertain requests or close peers."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import httpx

from atellagent_client.sdk.gateway.session import GatewaySession


class _AsyncLease:
    def __init__(self, request):
        self.request = request
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True


class _SyncLease:
    def __init__(self, request):
        self.request = request
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


def _session(client, manager=None):
    manager = manager or SimpleNamespace()
    async_leases = []
    sync_leases = []

    def async_client():
        lease = _AsyncLease(client.request)
        async_leases.append(lease)
        return lease

    def sync_client():
        lease = _SyncLease(client.request)
        sync_leases.append(lease)
        return lease

    manager.create_async_client = Mock(side_effect=async_client)
    manager.create_sync_client = Mock(side_effect=sync_client)
    session = GatewaySession(
        config=SimpleNamespace(), auth_manager=SimpleNamespace(invalidate_token=Mock()),
        http_client_manager=manager,
        oauth_http_client_manager=SimpleNamespace(), base_url="https://gateway.example",
    )
    session._auth_headers_async = AsyncMock(return_value={})
    session._auth_headers_sync = Mock(return_value={})
    session._test_async_leases = async_leases
    session._test_sync_leases = sync_leases
    return session


class GatewayRetrySafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_gateway_request_owns_and_closes_its_transport(self):
        client = SimpleNamespace(request=AsyncMock(return_value=httpx.Response(204)))
        session = _session(client)
        await session.request_authenticated("POST", "https://gateway.example/one")
        await session.request_authenticated("POST", "https://gateway.example/two")
        self.assertEqual(len(session._test_async_leases), 2)
        self.assertIsNot(session._test_async_leases[0], session._test_async_leases[1])
        self.assertTrue(all(lease.closed for lease in session._test_async_leases))
        session.http_client_manager.create_async_client.assert_called()

    async def test_async_uncertain_delivery_is_not_retried(self):
        for error_type in (httpx.ReadError, httpx.ReadTimeout, httpx.WriteError,
                           httpx.WriteTimeout, httpx.RemoteProtocolError):
            with self.subTest(error=error_type):
                client = SimpleNamespace(request=AsyncMock(side_effect=error_type("transport failed")))
                manager = SimpleNamespace(reset_async_client=AsyncMock())
                session = _session(client, manager)
                with self.assertRaises(error_type):
                    await session.request_authenticated("POST", "https://gateway.example/operation")
                self.assertEqual(client.request.await_count, 1)
                self.assertTrue(session._test_async_leases[0].closed)
                manager.reset_async_client.assert_not_awaited()

    def test_sync_uncertain_delivery_is_not_retried(self):
        for error_type in (httpx.ReadError, httpx.ReadTimeout, httpx.WriteError,
                           httpx.WriteTimeout, httpx.RemoteProtocolError):
            with self.subTest(error=error_type):
                client = SimpleNamespace(request=Mock(side_effect=error_type("transport failed")))
                manager = SimpleNamespace(reset_sync_client=Mock())
                session = _session(client, manager)
                with self.assertRaises(error_type):
                    session.request_authenticated_sync("POST", "https://gateway.example/operation")
                self.assertEqual(client.request.call_count, 1)
                self.assertTrue(session._test_sync_leases[0].closed)
                manager.reset_sync_client.assert_not_called()

    async def test_async_connect_recovery_is_bounded_and_does_not_reset_pool(self):
        for error_type in (httpx.ConnectError, httpx.ConnectTimeout):
            for permanent in (False, True):
                with self.subTest(error=error_type, permanent=permanent):
                    expected = httpx.Response(204)
                    client = SimpleNamespace(request=AsyncMock(side_effect=[
                        error_type("connection failed"),
                        error_type("connection failed") if permanent else expected,
                    ]))
                    manager = SimpleNamespace(reset_async_client=AsyncMock())
                    session = _session(client, manager)
                    if permanent:
                        with self.assertRaises(error_type):
                            await session.request_authenticated("POST", "https://gateway.example/operation")
                    else:
                        self.assertIs(await session.request_authenticated(
                            "POST", "https://gateway.example/operation"), expected)
                    self.assertEqual(client.request.await_count, 2)
                    self.assertEqual(len(session._test_async_leases), 2)
                    self.assertTrue(all(lease.closed for lease in session._test_async_leases))
                    manager.reset_async_client.assert_not_awaited()

    def test_sync_connect_recovery_is_bounded_and_does_not_reset_pool(self):
        client = SimpleNamespace(request=Mock(side_effect=httpx.ConnectError("connection failed")))
        manager = SimpleNamespace(reset_sync_client=Mock())
        session = _session(client, manager)
        with self.assertRaises(httpx.ConnectError):
            session.request_authenticated_sync("POST", "https://gateway.example/operation")
        self.assertEqual(client.request.call_count, 2)
        self.assertEqual(len(session._test_sync_leases), 2)
        self.assertTrue(all(lease.closed for lease in session._test_sync_leases))
        manager.reset_sync_client.assert_not_called()

    async def test_one_failed_request_does_not_close_a_concurrent_request(self):
        neighbor_started = asyncio.Event()
        release_neighbor = asyncio.Event()
        pool_closed = False

        async def reset_pool():
            nonlocal pool_closed
            pool_closed = True

        async def request(_method, url, **_kwargs):
            if url.endswith("/neighbor"):
                neighbor_started.set()
                await release_neighbor.wait()
                if pool_closed:
                    raise httpx.ReadError("pool closed during request")
                return httpx.Response(204)
            await neighbor_started.wait()
            raise httpx.ReadError("response lost")

        client = SimpleNamespace(request=AsyncMock(side_effect=request))
        manager = SimpleNamespace(reset_async_client=AsyncMock(side_effect=reset_pool))
        session = _session(client, manager)
        neighbor = asyncio.create_task(session.request_authenticated(
            "POST", "https://gateway.example/neighbor"))
        try:
            with self.assertRaises(httpx.ReadError):
                await session.request_authenticated("POST", "https://gateway.example/operation")
            self.assertEqual(len(session._test_async_leases), 2)
            self.assertEqual(
                sum(lease.closed for lease in session._test_async_leases), 1
            )
        finally:
            release_neighbor.set()
        result = await asyncio.wait_for(neighbor, timeout=1)
        self.assertEqual(result.status_code, 204)
        self.assertEqual(client.request.await_count, 2)
        self.assertEqual(len(session._test_async_leases), 2)
        self.assertTrue(all(lease.closed for lease in session._test_async_leases))
        manager.reset_async_client.assert_not_awaited()
