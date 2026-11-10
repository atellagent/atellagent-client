# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Optional Slack provider for the channel proxy."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import parse_qs
from uuid import NAMESPACE_URL, uuid5

import httpx

from .contracts import ChannelIngressDirectResponse, ChannelIngressSubmission


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(values: Any) -> Optional[str]:
    if isinstance(values, list):
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None
    if values is None:
        return None
    text = str(values).strip()
    return text or None


def _parse_slack_locator(value: Any) -> tuple[Optional[str], Optional[str], Optional[str]]:
    text = _first(value)
    if not text:
        return None, None, None
    if not text.lower().startswith("slack:"):
        return text, None, None
    parts = text.split(":", 2)
    if len(parts) != 3:
        return text, None, None
    channel_id = _first(parts[1])
    thread_ts = _first(parts[2])
    return text, channel_id, thread_ts


def _headers_lower(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _json_loads(raw_body: bytes) -> Dict[str, Any]:
    if not raw_body:
        return {}
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_slack_form_body(raw_body: bytes) -> Dict[str, Any]:
    if not raw_body:
        return {}
    try:
        parsed = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return {}

    payload_json = _first(parsed.get("payload"))
    if payload_json:
        try:
            payload = json.loads(payload_json)
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            return {
                "type": "interactive",
                "payload": payload,
                "form": {key: values if len(values) != 1 else values[0] for key, values in parsed.items()},
            }

    return {key: values if len(values) != 1 else values[0] for key, values in parsed.items()}


class SlackChannelAdapter:
    """First-class Slack adapter for Events API ingress and message egress."""

    channel_type = "slack"
    provider_key = "slack"
    display_name = "Slack"
    supported_ingress_modes = ["events_api", "url_verification", "interactivity", "slash_commands"]
    supported_actions = [
        {
            "name": "send_message",
            "label": "Send Message",
            "description": "Post a message to a Slack channel or thread via chat.postMessage",
        }
    ]

    def __init__(
        self,
        *,
        signing_secret: Optional[str] = None,
        bot_token: Optional[str] = None,
        adapter_key: str = "slack_events",
        verify_signatures: bool = True,
        signature_tolerance_seconds: int = 300,
        api_base_url: str = "https://slack.com/api",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.signing_secret = str(signing_secret or "")
        self.bot_token = str(bot_token or "")
        self.adapter_key = str(adapter_key or "slack_events")
        self.verify_signatures = bool(verify_signatures)
        self.signature_tolerance_seconds = int(signature_tolerance_seconds)
        self.api_base_url = str(api_base_url or "https://slack.com/api").rstrip("/")
        self.timeout_seconds = float(timeout_seconds)

    def _verify_slack_signature(
        self,
        *,
        raw_body: Optional[bytes],
        headers: Optional[Dict[str, str]],
    ) -> None:
        if not self.verify_signatures:
            return
        if not self.signing_secret:
            raise ValueError("Slack signing_secret is required when verify_signatures=True")
        if raw_body is None:
            raise ValueError("Slack ingress signature verification requires raw_body")

        hdrs = _headers_lower(headers)
        timestamp = hdrs.get("x-slack-request-timestamp")
        signature = hdrs.get("x-slack-signature")
        if not timestamp or not signature:
            raise ValueError("Missing Slack signature headers")

        try:
            ts_value = int(timestamp)
        except (TypeError, ValueError):
            raise ValueError("Invalid Slack request timestamp") from None
        now = int(time.time())
        if abs(now - ts_value) > self.signature_tolerance_seconds:
            raise ValueError("Slack request timestamp outside allowed tolerance window")

        base = f"v0:{timestamp}:".encode("utf-8") + raw_body
        digest = hmac.new(self.signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
        expected = f"v0={digest}"
        if not hmac.compare_digest(expected, signature):
            raise ValueError("Slack request signature verification failed")

    @staticmethod
    def _event_id_from_payload(payload: Dict[str, Any]) -> str:
        if payload.get("event_id"):
            return str(payload["event_id"])
        event = _coerce_dict(payload.get("event"))
        if event.get("client_msg_id"):
            return str(event["client_msg_id"])
        if event.get("event_ts"):
            return f"slack_evt_{event.get('event_ts')}"
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:24]
        return f"slack_evt_{digest}"

    @staticmethod
    def _build_event_callback_submission(
        payload: Dict[str, Any],
        *,
        adapter_key: str,
    ) -> ChannelIngressSubmission:
        event = _coerce_dict(payload.get("event"))
        team_id = payload.get("team_id") or _coerce_dict(payload.get("authorizations")).get("team_id")
        user_id = event.get("user") or event.get("bot_id") or payload.get("user_id")
        channel_id = event.get("channel") or payload.get("channel_id")
        thread_ts = event.get("thread_ts") or event.get("ts")
        text = (
            event.get("text")
            or _coerce_dict(event.get("message")).get("text")
            or payload.get("text")
            or ""
        )
        event_id = SlackChannelAdapter._event_id_from_payload(payload)
        correlation_id = None
        if channel_id and thread_ts:
            correlation_id = f"slack:{channel_id}:{thread_ts}"
        elif channel_id:
            correlation_id = f"slack:{channel_id}:{event_id}"

        canonical_event: Dict[str, Any] = {
            "event_id": event_id,
            "channel_type": "slack",
            "provider_key": "slack",
            "adapter_key": adapter_key,
            "received_at": _now_iso(),
            "event_type": f"slack.{payload.get('type') or 'event'}",
            "routing": {
                "correlation_id": correlation_id,
                "channel_thread_id": thread_ts,
                "conversation_id": channel_id,
            },
            "actor": {
                "provider": "slack",
                "team_id": str(team_id) if team_id else None,
                "user_id": str(user_id) if user_id else None,
            },
            "message": {
                "text": str(text or ""),
                "content": str(text or ""),
                "provider_message_ts": str(event.get("ts") or "") or None,
                "subtype": event.get("subtype"),
            },
            "payload": payload,
            "raw_event": payload,
        }
        canonical_event["actor"] = {k: v for k, v in canonical_event["actor"].items() if v is not None}
        canonical_event["message"] = {k: v for k, v in canonical_event["message"].items() if v is not None}

        input_data: Dict[str, Any] = {}
        if text:
            input_data["text"] = str(text)
        if channel_id:
            input_data["channel_id"] = str(channel_id)
        if thread_ts:
            input_data["thread_ts"] = str(thread_ts)
        # Propagate provider thread routing as explicit channel thread identity.
        if correlation_id:
            input_data["channel_thread_id"] = str(correlation_id)
        elif thread_ts:
            input_data["channel_thread_id"] = str(thread_ts)

        return ChannelIngressSubmission(
            event=canonical_event,
            input_data=input_data,
            channel_type="slack",
            provider_key="slack",
            adapter_key=adapter_key,
            idempotency_key=event_id,
        )

    def normalize_ingress_event(
        self,
        raw_event: Dict[str, Any],
        *,
        headers: Optional[Dict[str, str]] = None,
        raw_body: Optional[bytes] = None,
        content_type: Optional[str] = None,
    ) -> ChannelIngressSubmission:
        self._verify_slack_signature(raw_body=raw_body, headers=headers)

        normalized_content_type = str(content_type or "").lower()
        if "application/x-www-form-urlencoded" in normalized_content_type:
            payload = _parse_slack_form_body(raw_body or b"")
        elif "json" in normalized_content_type:
            payload = _json_loads(raw_body or b"") or _coerce_dict(raw_event)
        else:
            payload = _coerce_dict(raw_event)

        if str(payload.get("type") or "").strip().lower() == "url_verification":
            challenge = payload.get("challenge")
            if challenge is None:
                raise ValueError("Slack url_verification payload missing challenge")
            return ChannelIngressDirectResponse(
                body={"challenge": challenge},
                status_code=200,
                media_type="application/json",
            )

        # Events API callback: main supported ingress mode for v1.
        if str(payload.get("type") or "").strip().lower() == "event_callback":
            return self._build_event_callback_submission(payload, adapter_key=self.adapter_key)

        # Slash commands / interactions can still be normalized and forwarded, but caller should
        # ensure Slack UX does not require a custom immediate response body.
        channel_id = (
            payload.get("channel_id")
            or _coerce_dict(payload.get("channel")).get("id")
            or _coerce_dict(_coerce_dict(payload.get("payload")).get("channel")).get("id")
        )
        user_id = payload.get("user_id") or _coerce_dict(payload.get("user")).get("id")
        team_id = payload.get("team_id") or _coerce_dict(payload.get("team")).get("id")
        text = (
            payload.get("text")
            or _coerce_dict(payload.get("payload")).get("text")
            or _coerce_dict(_coerce_dict(payload.get("payload")).get("message")).get("text")
            or ""
        )
        trigger_id = payload.get("trigger_id")
        event_id = (
            str(trigger_id)
            if trigger_id
            else f"slack_evt_{hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:24]}"
        )
        canonical_event = {
            "event_id": event_id,
            "channel_type": "slack",
            "provider_key": "slack",
            "adapter_key": self.adapter_key,
            "received_at": _now_iso(),
            "event_type": f"slack.{payload.get('type') or 'interaction'}",
            "routing": {
                "correlation_id": f"slack:{channel_id}:{event_id}" if channel_id else event_id,
                "channel_thread_id": f"slack:{channel_id}:{event_id}" if channel_id else event_id,
                "conversation_id": channel_id,
            },
            "actor": {
                "provider": "slack",
                "team_id": str(team_id) if team_id else None,
                "user_id": str(user_id) if user_id else None,
            },
            "message": {"text": str(text or ""), "content": str(text or "")},
            "payload": payload,
            "raw_event": payload,
        }
        canonical_event["actor"] = {k: v for k, v in canonical_event["actor"].items() if v is not None}
        return ChannelIngressSubmission(
            event=canonical_event,
            input_data={
                **({"text": str(text)} if text else {}),
                **({"channel_id": str(channel_id)} if channel_id else {}),
                **(
                    {
                        "channel_thread_id": (
                            f"slack:{channel_id}:{event_id}" if channel_id else event_id
                        )
                    }
                    if event_id
                    else {}
                ),
            },
            channel_type="slack",
            provider_key="slack",
            adapter_key=self.adapter_key,
            idempotency_key=event_id,
        )

    @staticmethod
    def _resolve_send_message_target(
        payload: Dict[str, Any],
        metadata: Dict[str, Any],
        envelope: Dict[str, Any],
    ) -> Dict[str, Optional[str]]:
        routing = _coerce_dict(envelope.get("routing"))
        channel_obj = _coerce_dict(envelope.get("channel"))
        target = _coerce_dict(payload.get("target"))
        channel_id: Optional[str] = None
        thread_ts: Optional[str] = None

        channel_candidates = (
            payload.get("channel"),
            payload.get("channel_id"),
            payload.get("conversation_id"),
            target.get("channel"),
            target.get("channel_id"),
            target.get("conversation_id"),
            metadata.get("resource_id"),
            metadata.get("destination_id"),
            metadata.get("conversation_id"),
            routing.get("conversation_id"),
            channel_obj.get("conversation_id"),
            routing.get("correlation_id"),
        )
        for candidate in channel_candidates:
            normalized, parsed_channel, parsed_thread = _parse_slack_locator(candidate)
            if not normalized:
                continue
            if not channel_id:
                channel_id = parsed_channel or normalized
            if not thread_ts and parsed_thread:
                thread_ts = parsed_thread
            if channel_id and thread_ts:
                break

        thread_candidates = (
            payload.get("thread_ts"),
            payload.get("thread_id"),
            target.get("thread_ts"),
            target.get("thread_id"),
            metadata.get("thread_ts"),
            metadata.get("thread_id"),
            metadata.get("channel_thread_id"),
            routing.get("channel_thread_id"),
            routing.get("thread_ts"),
            routing.get("correlation_id"),
        )
        for candidate in thread_candidates:
            normalized, parsed_channel, parsed_thread = _parse_slack_locator(candidate)
            if not normalized:
                continue
            if not channel_id and parsed_channel:
                channel_id = parsed_channel
            thread_ts = parsed_thread or normalized
            break

        text = payload.get("text")
        return {
            "channel": str(channel_id) if channel_id else None,
            "thread_ts": str(thread_ts) if thread_ts else None,
            "text": str(text) if text is not None else None,
        }

    async def dispatch_egress_action(
        self,
        *,
        action: str,
        payload: Dict[str, Any],
        metadata: Dict[str, Any],
        envelope: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        del headers  # provider auth is handled via Slack bot token, not forwarded gateway headers
        action_name = str(action or "").strip()
        if action_name not in {"send_message", "chat.postMessage", "slack.chat.postMessage"}:
            raise ValueError(f"Slack adapter does not support action={action_name!r}")
        if not self.bot_token:
            raise ValueError("Slack bot_token is required for egress dispatch")

        target = self._resolve_send_message_target(payload, metadata, envelope)
        if not target["channel"]:
            raise ValueError(
                "Slack send_message requires a channel id "
                "(payload.channel/channel_id/conversation_id, metadata.resource_id/destination_id, or routing correlation)"
            )

        request_payload: Dict[str, Any] = {"channel": target["channel"]}
        if target["thread_ts"]:
            request_payload["thread_ts"] = target["thread_ts"]

        blocks = payload.get("blocks")
        if isinstance(blocks, list) and blocks:
            request_payload["blocks"] = blocks

        attachments = payload.get("attachments")
        if isinstance(attachments, list) and attachments:
            request_payload["attachments"] = attachments

        text = target["text"] or ""
        if not text.strip():
            raise ValueError("Slack send_message requires non-empty payload.text")
        request_payload["text"] = text
        effect_key = str(envelope.get("event_id") or "").strip()
        if not effect_key:
            raise ValueError("Slack connected action requires an event_id")
        request_payload["client_msg_id"] = str(
            uuid5(NAMESPACE_URL, f"atellagent:slack:{effect_key}")
        )

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.api_base_url}/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {self.bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=request_payload,
            )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Slack API returned non-object JSON")
        if not body.get("ok"):
            error_code = str(body.get("error") or "slack_api_error")
            raise ValueError(f"Slack API chat.postMessage failed: {error_code}")

        return {
            "provider": "slack",
            "action": "send_message",
            "ok": True,
            "channel": body.get("channel") or target["channel"],
            "ts": body.get("ts"),
            "thread_ts": _coerce_dict(body.get("message")).get("thread_ts") or target["thread_ts"],
            "response": body,
        }


__all__ = ["SlackChannelAdapter"]
