# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Connected SDK lifecycle built on the canonical participant core."""

from __future__ import annotations

from typing import Any

from atellagent_client.connected import ConnectedParticipant
from atellagent_client.sdk.config import ServiceAccountConfig


class ConnectedSDKRuntime(ConnectedParticipant):
    """Mount handlers directly in a customer process and run the receive loop."""

    def __init__(self, config: ServiceAccountConfig, **kwargs: Any) -> None:
        if config.packaging != "sdk":
            raise ValueError("ConnectedSDKRuntime requires packaging: sdk")
        super().__init__(config, **kwargs)


__all__ = ["ConnectedSDKRuntime"]
