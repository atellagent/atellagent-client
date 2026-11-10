# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public contracts for channel-proxy adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Dict, Optional, Protocol, runtime_checkable


@dataclass
class ChannelIngressSubmission:
    """Canonical channel ingress submission forwarded to gateway /v1/channels/ingress."""

    event: Dict[str, Any]
    target: Dict[str, Any] = field(default_factory=dict)
    input_data: Dict[str, Any] = field(default_factory=dict)
    execution_config: Dict[str, Any] = field(default_factory=dict)
    channel_type: Optional[str] = None
    provider_key: Optional[str] = None
    adapter_key: Optional[str] = None
    idempotency_key: Optional[str] = None


@dataclass
class ChannelIngressDirectResponse:
    """Provider-facing direct HTTP response emitted by an ingress adapter."""

    body: Any
    status_code: int = 200
    headers: Dict[str, str] = field(default_factory=dict)
    media_type: Optional[str] = None


AdapterResult = Dict[str, Any]
ChannelIngressNormalizeResult = ChannelIngressSubmission | ChannelIngressDirectResponse
MaybeAwaitableSubmission = ChannelIngressNormalizeResult | Dict[str, Any] | Awaitable[
    ChannelIngressNormalizeResult | Dict[str, Any]
]
MaybeAwaitableResult = AdapterResult | Awaitable[AdapterResult]


@runtime_checkable
class ChannelIngressAdapter(Protocol):
    channel_type: str
    provider_key: Optional[str]
    adapter_key: Optional[str]

    def normalize_ingress_event(
        self,
        raw_event: Dict[str, Any],
        *,
        headers: Optional[Dict[str, str]] = None,
        raw_body: Optional[bytes] = None,
        content_type: Optional[str] = None,
    ) -> MaybeAwaitableSubmission: ...


@runtime_checkable
class ChannelEgressAdapter(Protocol):
    channel_type: str
    provider_key: Optional[str]
    adapter_key: Optional[str]

    def dispatch_egress_action(
        self,
        *,
        action: str,
        payload: Dict[str, Any],
        metadata: Dict[str, Any],
        envelope: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> MaybeAwaitableResult:
        """
        Dispatch a gateway egress action to the provider.

        Contract for `action="send_message"`:
        - adapters must render user-visible message text from `payload["text"]` only.
        - adapters must not read alternate nested fields for display text.
        """
        ...
