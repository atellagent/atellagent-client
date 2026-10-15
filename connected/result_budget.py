"""Prepare committable connected results without replaying completed work."""
from __future__ import annotations

import json
from typing import Any

from .contracts import ConnectedHandlerResult, ConnectedMessage

# Connected result bodies and fields are each limited to one MiB. Larger
# results require a protocol/artifact change, not a completed-handler retry.
RESULT_REQUEST_MAX_BYTES = 1024 * 1024
RESULT_FIELD_MAX_BYTES = 1024 * 1024


def _fits_json(value: Any, maximum: int, *, ensure_ascii: bool) -> bool:
    encoder = json.JSONEncoder(ensure_ascii=ensure_ascii, sort_keys=ensure_ascii,
                              separators=(",", ":"), allow_nan=False)
    remaining = maximum
    for chunk in encoder.iterencode(value):
        # UTF-8 is at least one byte per code point. Avoid allocating a second
        # large byte string for an already oversized encoder chunk.
        if len(chunk) > remaining:
            return False
        remaining -= len(chunk.encode("utf-8"))
        if remaining < 0:
            return False
    return True


def _body(message: ConnectedMessage, result: ConnectedHandlerResult) -> dict[str, Any]:
    return {
        "lease_id": message.lease.lease_id,
        "lease_token": message.lease.lease_token,
        "terminal_status": result.terminal_status,
        "result_schema": result.result_schema,
        "result_payload": result.result_payload,
        "evidence_payload": result.evidence_payload,
    }


def build_result_commit_payload(message: ConnectedMessage,
                                result: ConnectedHandlerResult) -> dict[str, Any]:
    body = _body(message, result)
    code = "connected_result_too_large"
    error_type = "ConnectedResultTooLarge"
    detail = "Handler may have completed, but its result exceeds the connected protocol limit."
    try:
        fits = (_fits_json(result.result_payload, RESULT_FIELD_MAX_BYTES, ensure_ascii=True)
                and _fits_json(result.evidence_payload, RESULT_FIELD_MAX_BYTES, ensure_ascii=True)
                and _fits_json(body, RESULT_REQUEST_MAX_BYTES, ensure_ascii=False))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        fits = False
        code = "connected_result_invalid"
        error_type = "ConnectedResultInvalid"
        detail = "Handler may have completed, but its result is not valid connected protocol JSON."
    if fits:
        return body
    if message.operation in {"workflow.execute", "workflow.resume", "workflow.cancel"}:
        failure = ConnectedHandlerResult(
            terminal_status="failed", result_schema="atellagent.connected.workflow-outcome.v1",
            result_payload={"protocol_version": "v1", "outcome": "failed",
                            "error": {"code": code, "detail": detail}},
        )
    else:
        failure = ConnectedHandlerResult(
            terminal_status="failed", result_schema="atellagent.connected.error.v1",
            result_payload={"error_type": error_type, "code": code, "detail": detail},
        )
    return _body(message, failure)
