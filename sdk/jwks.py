# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""
JWKS fetching helpers for SDK validators.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from .http import HTTPClientManager


class JWKSFetcher:
    def __init__(
        self,
        http_client: HTTPClientManager,
        ttl_seconds: int = 3600,
        max_entries: int = 256,
    ) -> None:
        self._http = http_client
        self._ttl = ttl_seconds
        self._max_entries = max(1, int(max_entries))
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def _prune_cache(self, now: float) -> None:
        expired_urls = [
            cache_url
            for cache_url, entry in self._cache.items()
            if (now - float(entry.get("timestamp", 0))) >= float(entry.get("ttl", 0))
        ]
        for cache_url in expired_urls:
            self._cache.pop(cache_url, None)

        overflow = len(self._cache) - self._max_entries
        if overflow > 0:
            oldest_urls = sorted(
                self._cache,
                key=lambda cache_url: float(
                    self._cache[cache_url].get("timestamp", 0)
                ),
            )[:overflow]
            for cache_url in oldest_urls:
                self._cache.pop(cache_url, None)

    async def get(self, url: str, *, force_refresh: bool = False) -> Dict[str, Any]:
        current_time = time.time()
        cached = self._cache.get(url)
        if not force_refresh and cached and (current_time - cached["timestamp"]) < cached["ttl"]:
            return cached["keys"]
        async with self._lock:
            current_time = time.time()
            self._prune_cache(current_time)
            cached = self._cache.get(url)
            if not force_refresh and cached and (current_time - cached["timestamp"]) < cached["ttl"]:
                return cached["keys"]
            client = await self._http.get_async_client()
            response = await client.get(url)
            if self._http.http2 and response.http_version != "HTTP/2":
                raise RuntimeError("JWKS response did not use HTTP/2")
            response.raise_for_status()
            jwks = response.json()
            self._cache[url] = {
                "keys": jwks,
                "timestamp": current_time,
                "ttl": self._ttl,
            }
            self._prune_cache(time.time())
            return jwks


def get_jwk_by_kid(jwks: Dict[str, Any], kid: str) -> Optional[Dict[str, Any]]:
    for jwk in jwks.get("keys", []):
        if jwk.get("kid") == kid:
            return jwk
    return None


__all__ = ["JWKSFetcher", "get_jwk_by_kid"]
