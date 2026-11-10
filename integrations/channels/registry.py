# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Explicit registry for channel-proxy adapters."""

from __future__ import annotations

import inspect
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from .contracts import ChannelIngressDirectResponse, ChannelIngressSubmission


def _norm(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _adapter_key(adapter: Any) -> str:
    return str(getattr(adapter, "adapter_key", "") or "").strip()


def _channel_type(adapter: Any) -> str:
    return _norm(getattr(adapter, "channel_type", ""))


def _provider_key(adapter: Any) -> str:
    return _norm(getattr(adapter, "provider_key", ""))


def _validate_egress_contract(*, action: str, payload: Dict[str, Any]) -> None:
    if _norm(action) != "send_message":
        return
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("send_message requires non-empty payload.text")


def _declared_action_names(adapter: Any) -> set[str]:
    """Return only actions the customer adapter explicitly declared."""
    return {
        str(entry.get("name") or "").strip()
        for entry in ChannelAdapterRegistry._adapter_inventory_entry(adapter).get("actions", [])
        if isinstance(entry, dict) and str(entry.get("name") or "").strip()
    }


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class ChannelAdapterRegistry:
    """Resolves ingress/egress handlers by adapter key or channel/provider tuple."""

    def __init__(self) -> None:
        self._by_adapter_key: Dict[str, Any] = {}
        self._by_type_provider: Dict[Tuple[str, str], Any] = {}
        self._default_by_type: Dict[str, Any] = {}

    def register(self, adapter: Any) -> None:
        channel_type = _channel_type(adapter)
        if not channel_type:
            raise ValueError("Channel adapter must define channel_type")
        adapter_key = _adapter_key(adapter)
        provider_key = _provider_key(adapter)
        if adapter_key:
            self._by_adapter_key[adapter_key] = adapter
        if provider_key:
            self._by_type_provider[(channel_type, provider_key)] = adapter
        self._default_by_type[channel_type] = adapter

    def register_many(self, adapters: Iterable[Any]) -> None:
        for adapter in adapters:
            self.register(adapter)

    @staticmethod
    def _normalize_action_entry(value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, str):
            name = value.strip()
            if not name:
                return None
            return {"name": name, "label": name}
        if not isinstance(value, dict):
            return None
        name = str(value.get("name") or value.get("action") or value.get("id") or "").strip()
        if not name:
            return None
        payload: Dict[str, Any] = {"name": name}
        label = str(value.get("label") or value.get("title") or "").strip()
        if label:
            payload["label"] = label
        description = str(value.get("description") or "").strip()
        if description:
            payload["description"] = description
        category = str(value.get("category") or "").strip()
        if category:
            payload["category"] = category
        return payload

    @staticmethod
    def _adapter_inventory_entry(adapter: Any) -> Dict[str, Any]:
        describe_fn = getattr(adapter, "describe_adapter", None)
        described = describe_fn() if callable(describe_fn) else {}
        described = described if isinstance(described, dict) else {}

        raw_actions = described.get("actions")
        if raw_actions is None:
            raw_actions = getattr(adapter, "supported_actions", [])
        if not isinstance(raw_actions, list):
            raw_actions = []
        actions = [
            entry
            for entry in (
                ChannelAdapterRegistry._normalize_action_entry(item) for item in raw_actions
            )
            if entry
        ]

        supports_ingress = described.get("supports_ingress")
        if supports_ingress is None:
            supports_ingress = callable(getattr(adapter, "normalize_ingress_event", None))
        supports_egress = described.get("supports_egress")
        if supports_egress is None:
            supports_egress = callable(getattr(adapter, "dispatch_egress_action", None))

        entry: Dict[str, Any] = {
            "channel_type": _channel_type(adapter),
            "provider_key": _provider_key(adapter) or None,
            "adapter_key": _adapter_key(adapter) or None,
            "display_name": str(
                described.get("display_name")
                or getattr(adapter, "display_name", "")
                or adapter.__class__.__name__
            ),
            "supports_ingress": bool(supports_ingress),
            "supports_egress": bool(supports_egress),
            "actions": actions,
        }
        ingress_modes = described.get("ingress_modes") or getattr(adapter, "supported_ingress_modes", None)
        if isinstance(ingress_modes, list):
            entry["ingress_modes"] = [str(v).strip() for v in ingress_modes if str(v).strip()]
        return entry

    def list_inventory(self) -> list[Dict[str, Any]]:
        seen: set[int] = set()
        inventory: list[Dict[str, Any]] = []
        for adapter in self._by_adapter_key.values():
            key = id(adapter)
            if key in seen:
                continue
            seen.add(key)
            inventory.append(self._adapter_inventory_entry(adapter))
        for adapter in self._by_type_provider.values():
            key = id(adapter)
            if key in seen:
                continue
            seen.add(key)
            inventory.append(self._adapter_inventory_entry(adapter))
        for adapter in self._default_by_type.values():
            key = id(adapter)
            if key in seen:
                continue
            seen.add(key)
            inventory.append(self._adapter_inventory_entry(adapter))
        inventory.sort(
            key=lambda item: (
                str(item.get("channel_type") or ""),
                str(item.get("provider_key") or ""),
                str(item.get("adapter_key") or ""),
            )
        )
        return inventory

    def resolve(
        self,
        *,
        adapter_key: Optional[str] = None,
        channel_type: Optional[str] = None,
        provider_key: Optional[str] = None,
    ) -> Any:
        key = str(adapter_key or "").strip()
        if key and key in self._by_adapter_key:
            return self._by_adapter_key[key]
        ctype = _norm(channel_type)
        pkey = _norm(provider_key)
        if ctype and pkey and (ctype, pkey) in self._by_type_provider:
            return self._by_type_provider[(ctype, pkey)]
        if ctype and ctype in self._default_by_type:
            return self._default_by_type[ctype]
        raise KeyError(
            "No channel adapter registered for "
            f"adapter_key={adapter_key!r}, channel_type={channel_type!r}, provider_key={provider_key!r}"
        )

    async def normalize_ingress_event(
        self,
        *,
        raw_event: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        raw_body: Optional[bytes] = None,
        content_type: Optional[str] = None,
        adapter_key: Optional[str] = None,
        channel_type: Optional[str] = None,
        provider_key: Optional[str] = None,
    ) -> ChannelIngressSubmission | ChannelIngressDirectResponse:
        adapter = self.resolve(
            adapter_key=adapter_key,
            channel_type=channel_type,
            provider_key=provider_key,
        )
        normalize_fn = getattr(adapter, "normalize_ingress_event", None)
        if normalize_fn is None:
            raise AttributeError(f"Adapter {_adapter_key(adapter) or adapter!r} lacks normalize_ingress_event")
        submission = await _await_if_needed(
            normalize_fn(
                raw_event,
                headers=headers,
                raw_body=raw_body,
                content_type=content_type,
            )
        )
        if is_dataclass(submission):
            if isinstance(submission, ChannelIngressDirectResponse):
                return submission
            if isinstance(submission, ChannelIngressSubmission):
                result = submission
            else:
                result = ChannelIngressSubmission(**asdict(submission))
        elif isinstance(submission, dict):
            result = ChannelIngressSubmission(**submission)
        else:
            raise TypeError("Channel ingress adapter must return ChannelIngressSubmission or dict")
        if not result.channel_type:
            result.channel_type = getattr(adapter, "channel_type", None)
        if not result.provider_key:
            result.provider_key = getattr(adapter, "provider_key", None)
        if not result.adapter_key:
            result.adapter_key = getattr(adapter, "adapter_key", None)
        return result

    async def dispatch_egress_action(
        self,
        *,
        envelope: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        action = str(envelope.get("action") or "")
        payload = (
            envelope.get("payload")
            if isinstance(envelope.get("payload"), dict)
            else {}
        )
        _validate_egress_contract(action=action, payload=payload)
        channel = envelope.get("channel") if isinstance(envelope, dict) else {}
        channel = channel if isinstance(channel, dict) else {}
        adapter = self.resolve(
            adapter_key=channel.get("adapter_key"),
            channel_type=channel.get("channel_type"),
            provider_key=channel.get("provider_key"),
        )
        if action not in _declared_action_names(adapter):
            raise ValueError("Channel action is not declared by the selected adapter")
        dispatch_fn = getattr(adapter, "dispatch_egress_action", None)
        if dispatch_fn is None:
            raise AttributeError(f"Adapter {_adapter_key(adapter) or adapter!r} lacks dispatch_egress_action")
        result = await _await_if_needed(
            dispatch_fn(
                action=action,
                payload=payload,
                metadata=envelope.get("metadata")
                if isinstance(envelope.get("metadata"), dict)
                else {},
                envelope=envelope,
                headers=headers,
            )
        )
        return result if isinstance(result, dict) else {"result": result}
