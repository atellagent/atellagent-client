# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public entrypoints, contracts, and optional providers for the channel proxy."""

from .contracts import (
    ChannelEgressAdapter,
    ChannelIngressDirectResponse,
    ChannelIngressAdapter,
    ChannelIngressSubmission,
)
from .registry import ChannelAdapterRegistry
from .slack import SlackChannelAdapter

__all__ = [
    "ChannelAdapterRegistry",
    "ChannelIngressSubmission",
    "ChannelIngressDirectResponse",
    "ChannelIngressAdapter",
    "ChannelEgressAdapter",
    "SlackChannelAdapter",
]
