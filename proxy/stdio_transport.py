# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md in this source tree.
"""Bound JSON-RPC lines while reusing the pinned SDK's process-tree lifecycle."""

from contextlib import asynccontextmanager
import sys

import anyio
from mcp import types as mcp_types
from mcp.client.stdio import (
    _create_platform_compatible_process, _get_executable_command,
    _stop_server_process, get_default_environment,
)
from mcp.shared.message import SessionMessage

MAX_STDIO_FRAME_BYTES = 1024 * 1024


@asynccontextmanager
async def bounded_stdio_client(parameters):
    # These process helpers come from the exactly pinned MCP SDK. They retain
    # Windows job ownership and bounded POSIX process-tree cleanup; no second
    # subprocess supervisor or retry loop is introduced.
    process = await _create_platform_compatible_process(
        command=_get_executable_command(parameters.command), args=parameters.args,
        env=get_default_environment() | (parameters.env or {}), errlog=sys.stderr, cwd=parameters.cwd)
    incoming_writer, incoming = anyio.create_memory_object_stream(0)
    outgoing, outgoing_reader = anyio.create_memory_object_stream(0)

    async def read():
        pending = bytearray()
        async with incoming_writer:
            while True:
                try:
                    chunk = await process.stdout.receive(16384)
                except anyio.EndOfStream:
                    if pending.strip():
                        raise ValueError("MCP stdio ended with an incomplete frame")
                    return
                pending.extend(chunk)
                while True:
                    end = pending.find(b"\n")
                    if end < 0:
                        if len(pending)>MAX_STDIO_FRAME_BYTES:
                            raise ValueError("MCP stdio frame exceeded its byte limit")
                        break
                    if end>MAX_STDIO_FRAME_BYTES:
                        raise ValueError("MCP stdio frame exceeded its byte limit")
                    line = bytes(pending[:end]); del pending[:end+1]
                    if not line.strip(): continue
                    try:
                        message = mcp_types.jsonrpc_message_adapter.validate_json(line, by_name=False)
                    except ValueError:
                        raise ValueError("MCP stdio frame is invalid JSON-RPC") from None
                    await incoming_writer.send(SessionMessage(message))

    async def write():
        async with outgoing_reader:
            async for item in outgoing_reader:
                line = item.message.model_dump_json(by_alias=True, exclude_unset=True).encode("utf-8")
                if len(line)>MAX_STDIO_FRAME_BYTES:
                    raise ValueError("MCP stdio request exceeded its byte limit")
                await process.stdin.send(line+b"\n")

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(read)
            group.start_soon(write)
            try:
                yield incoming, outgoing
            finally:
                group.cancel_scope.cancel()
    finally:
        with anyio.CancelScope(shield=True):
            await _stop_server_process(process)
            for stream in (incoming_writer, incoming, outgoing, outgoing_reader):
                await stream.aclose()
