# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Strict public contracts for the connected participant protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Mapping, Optional
from uuid import UUID


class ConnectedProtocolError(RuntimeError):
    """A gateway envelope or local handler violated the v1 contract."""


def _object(value: Any, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConnectedProtocolError(f"{field_name} must be an object")
    return dict(value)


def _strict_keys(value: Mapping[str, Any], expected: set[str], field_name: str) -> None:
    extra = set(value) - expected
    if extra:
        raise ConnectedProtocolError(
            f"{field_name} contains unsupported fields: {', '.join(sorted(extra))}"
        )


def _text(value: Any, field_name: str, *, maximum: int = 255) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ConnectedProtocolError(f"{field_name} is invalid")
    return text


def _uuid(value: Any, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ConnectedProtocolError(f"{field_name} must be a UUID") from exc


def _datetime(value: Any, field_name: str) -> datetime:
    text = _text(value, field_name)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConnectedProtocolError(f"{field_name} must be an ISO timestamp") from exc


@dataclass(frozen=True)
class ConnectedLease:
    lease_id: str
    lease_token: str = field(repr=False)
    attempt_number: int
    expires_at: datetime


@dataclass(frozen=True)
class ConnectedMessage:
    message_id: str
    kind: str
    operation: str
    protocol_version: str
    execution_id: Optional[str]
    execution_attempt_id: Optional[str]
    idempotency_key: str
    payload_schema: str
    payload: Dict[str, Any]
    capability: str = field(repr=False)
    lease: ConnectedLease

    def delivery(self) -> "ConnectedDelivery":
        return ConnectedDelivery(
            message_id=self.message_id,
            kind=self.kind,
            operation=self.operation,
            execution_id=self.execution_id,
            execution_attempt_id=self.execution_attempt_id,
            idempotency_key=self.idempotency_key,
            payload_schema=self.payload_schema,
            payload=dict(self.payload),
            delivery_attempt=self.lease.attempt_number,
            lease_expires_at=self.lease.expires_at,
        )


@dataclass(frozen=True)
class ConnectedDelivery:
    """Secret-free delivery view passed to customer handlers."""

    message_id: str
    kind: str
    operation: str
    execution_id: Optional[str]
    execution_attempt_id: Optional[str]
    idempotency_key: str
    payload_schema: str
    payload: Dict[str, Any]
    delivery_attempt: int
    lease_expires_at: datetime


@dataclass(frozen=True)
class ConnectedHandlerResult:
    terminal_status: str
    result_schema: str
    result_payload: Dict[str, Any] = field(default_factory=dict)
    evidence_payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.terminal_status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("terminal_status must be succeeded, failed, or cancelled")
        if not str(self.result_schema or "").strip():
            raise ValueError("result_schema is required")

    @classmethod
    def succeeded(
        cls,
        *,
        result_schema: str,
        result_payload: Optional[Mapping[str, Any]] = None,
        evidence_payload: Optional[Mapping[str, Any]] = None,
    ) -> "ConnectedHandlerResult":
        return cls(
            terminal_status="succeeded",
            result_schema=result_schema,
            result_payload=dict(result_payload or {}),
            evidence_payload=dict(evidence_payload or {}),
        )


def parse_connected_message(value: Any) -> ConnectedMessage:
    envelope = _object(value, "message")
    expected = {
        "message_id",
        "kind",
        "operation",
        "protocol_version",
        "execution_id",
        "execution_attempt_id",
        "idempotency_key",
        "payload_schema",
        "payload",
        "capability",
        "lease",
    }
    _strict_keys(envelope, expected, "message")
    if envelope.get("protocol_version") != "v1":
        raise ConnectedProtocolError("message protocol_version must be v1")
    kind = _text(envelope.get("kind"), "message.kind", maximum=32)
    if kind not in {"action", "cancel", "resume", "control"}:
        raise ConnectedProtocolError("message.kind is unsupported")
    lease_value = _object(envelope.get("lease"), "message.lease")
    _strict_keys(
        lease_value,
        {"lease_id", "lease_token", "attempt_number", "expires_at"},
        "message.lease",
    )
    try:
        attempt_number = int(lease_value.get("attempt_number"))
    except (TypeError, ValueError) as exc:
        raise ConnectedProtocolError("message.lease.attempt_number is invalid") from exc
    if attempt_number < 1:
        raise ConnectedProtocolError("message.lease.attempt_number is invalid")
    lease = ConnectedLease(
        lease_id=_uuid(lease_value.get("lease_id"), "message.lease.lease_id"),
        lease_token=_text(
            lease_value.get("lease_token"),
            "message.lease.lease_token",
            maximum=256,
        ),
        attempt_number=attempt_number,
        expires_at=_datetime(
            lease_value.get("expires_at"),
            "message.lease.expires_at",
        ),
    )
    if len(lease.lease_token) < 32:
        raise ConnectedProtocolError("message.lease.lease_token is invalid")
    return ConnectedMessage(
        message_id=_uuid(envelope.get("message_id"), "message.message_id"),
        kind=kind,
        operation=_text(envelope.get("operation"), "message.operation", maximum=64),
        protocol_version="v1",
        execution_id=(
            _text(envelope.get("execution_id"), "message.execution_id")
            if envelope.get("execution_id") is not None
            else None
        ),
        execution_attempt_id=(
            _text(
                envelope.get("execution_attempt_id"),
                "message.execution_attempt_id",
            )
            if envelope.get("execution_attempt_id") is not None
            else None
        ),
        idempotency_key=_text(
            envelope.get("idempotency_key"),
            "message.idempotency_key",
        ),
        payload_schema=_text(
            envelope.get("payload_schema"),
            "message.payload_schema",
            maximum=128,
        ),
        payload=_object(envelope.get("payload"), "message.payload"),
        capability=_text(
            envelope.get("capability"),
            "message.capability",
            maximum=16384,
        ),
        lease=lease,
    )


__all__ = [
    "ConnectedDelivery",
    "ConnectedHandlerResult",
    "ConnectedLease",
    "ConnectedMessage",
    "ConnectedProtocolError",
    "parse_connected_message",
]
