# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md in this source tree.
"""Bound HTTP JSON/SSE bytes before the MCP SDK buffers or decodes them."""

import httpx2
from .native_credentials import current_native_authorization

MAX_HTTP_RESPONSE_BYTES = 1024 * 1024


class _BoundedResponse(httpx2.AsyncByteStream):
    def __init__(self, stream):
        self._stream = stream

    async def __aiter__(self):
        received = 0
        async for chunk in self._stream:
            received += len(chunk)
            if received > MAX_HTTP_RESPONSE_BYTES:
                await self._stream.aclose()
                raise httpx2.ReadError("MCP response exceeded its byte limit")
            yield chunk

    async def aclose(self):
        await self._stream.aclose()


class BoundedHTTPTransport(httpx2.AsyncBaseTransport):
    def __init__(self, transport):
        self._transport = transport

    async def handle_async_request(self, request):
        request.headers["Accept-Encoding"] = "identity"
        # The SDK propagates each sender's context to its POST/resumption task.
        # Mutate only this request, never shared client headers or credentials.
        native=current_native_authorization()
        if native is not None: request.headers["Authorization"]=native
        response = await self._transport.handle_async_request(request)
        # Counting compressed bytes does not bound decoded memory. Refuse
        # unsolicited compression, instead of adding a second MCP decoder.
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            await response.aclose()
            raise httpx2.ReadError("Compressed MCP responses are unsupported")
        length = response.headers.get("content-length")
        if length is not None and (not length.isdecimal() or int(length) > MAX_HTTP_RESPONSE_BYTES):
            await response.aclose()
            raise httpx2.ReadError("MCP response exceeded its byte limit")
        response.stream = _BoundedResponse(response.stream)
        return response

    async def aclose(self):
        await self._transport.aclose()
