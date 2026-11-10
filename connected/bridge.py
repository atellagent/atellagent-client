# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Thin bridge lifecycle over the canonical connected participant core."""

from __future__ import annotations

from typing import Any

from atellagent_client.sdk.config import ServiceAccountConfig

from .participant import ConnectedParticipant


class ConnectedBridge(ConnectedParticipant):
    """Translate connected deliveries to customer-local targets via handlers."""

    def __init__(self, config: ServiceAccountConfig, **kwargs: Any) -> None:
        if config.packaging != "bridge":
            raise ValueError("ConnectedBridge requires packaging: bridge")
        super().__init__(config, **kwargs)


__all__ = ["ConnectedBridge"]
