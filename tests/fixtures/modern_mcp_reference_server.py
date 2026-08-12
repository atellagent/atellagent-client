# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Minimal stdio reference server for the pinned MCP 2026-07-28 contract."""

from __future__ import annotations

import json
import sys


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "server/discover":
        result = {
            "resultType": "complete",
            "supportedVersions": ["2026-07-28"],
            "cacheScope": "private",
            "ttlMs": 0,
            "capabilities": {"tools": {}},
        }
    elif method == "tools/list":
        result = {
            "resultType": "complete",
            "cacheScope": "private",
            "ttlMs": 0,
            "tools": [
                {
                    "name": "echo",
                    "description": "Returns a fixed successful result",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ],
        }
    elif method == "tools/call":
        result = {
            "resultType": "complete",
            "content": [{"type": "text", "text": "ok"}],
        }
    else:
        raise RuntimeError(f"unexpected MCP method: {method}")
    print(
        json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}),
        flush=True,
    )
