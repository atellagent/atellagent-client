# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK client cleanup and context manager methods."""

from __future__ import annotations


class ClientLifecycleMixin:
    def close(self):
        """Close HTTP client connections."""
        for manager in self._http_managers():
            manager.close_sync()

    async def close_async(self):
        """Close async HTTP client connections."""
        for manager in self._http_managers():
            await manager.close_async()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb):
        await self.close_async()


__all__ = ["ClientLifecycleMixin"]
